"""The effect grammar: one sentence in, one Effect tree out.

Each clause is a small function that either consumes a sentence and returns an
``Effect``, or consumes nothing and returns ``None``. They are tried in
registration order and the first that succeeds wins, so more specific patterns
register before more general ones - "destroy all creatures" is not a special
case of "destroy target creature", but "deal N damage to each opponent" *is*
close enough to "deal N damage to target creature" that order decides.

Every clause returns an opcode the engine executes. There is no clause here
whose output the engine cannot run, and the test suite proves it by walking the
registry against ``EXECUTORS``.
"""

from __future__ import annotations

import collections
from dataclasses import replace
from typing import Callable

from ..rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from ..rules.kernel.enums import CardType, Duration, Zone
from ..rules.kernel.query import (
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
    ValueKind,
)
from .nouns import (
    SELF_NOUNS,
    chosen_count,
    parse_comparison,
    parse_description,
    parse_object_filter,
    parse_player_filter,
    parse_target,
    parse_value,
    parse_zone,
)
from .tokens import Stream, TokenKind

Clause = Callable[[Stream], "Effect | None"]
CLAUSES: list[tuple[str, Clause]] = []

#: How many times each clause raised rather than returning cleanly. A raising
#: clause is a bug in the grammar, not a card the grammar cannot read, and the
#: two are indistinguishable from the outside without this.
CLAUSE_ERRORS: collections.Counter[str] = collections.Counter()

#: When true, a clause that raises propagates instead of being swallowed.
#: Tests turn this on so a broken rule fails loudly rather than quietly
#: reducing coverage.
STRICT_CLAUSES = False

YOU = PlayerFilter(PlayerScope.YOU)
SELF = ObjectFilter(source_only=True)
#: CR 115.4: "any target" means a creature, player, planeswalker or battle.
#: Modelled as the object types, because the engine's damage executor already
#: handles a player target through ``players``.
#:
#: Handed out as a fresh copy (``replace(ANY_TARGET)``) each time it is
#: read: two instructions holding the *same* filter object are one word
#: "target" read as two verbs (``compile._share_targets``), and two separate
#: "any target"s sharing this constant would be merged into one choice.
ANY_TARGET = ObjectFilter(
    types_any=CardType.CREATURE | CardType.PLANESWALKER | CardType.BATTLE,
    includes_players=True,
)


def clause(name: str):
    """Register a clause pattern. Order of registration is order of attempt."""

    def decorate(fn: Clause) -> Clause:
        CLAUSES.append((name, fn))
        return fn

    return decorate


def parse_effect(stream: Stream) -> Effect | None:
    """One effect, or ``None`` with the cursor untouched.

    The clause that consumes the **most tokens** wins, not the one that
    happens to be registered first. Oracle text is full of specific forms that
    begin exactly like general ones - "You may cast spells as though they had
    flash" starts like "You may <do something>", "You may play an additional
    land" likewise - and under first-match-wins the general clause ate the
    prefix and stranded the rest, failing an ability the grammar could read
    perfectly well.

    Registration order then decides ties only, which keeps the behaviour
    predictable when two clauses genuinely read the same span.
    """
    stream.skip_punct(",", ";")
    if stream.done:
        return None

    start = stream.mark()
    best: Effect | None = None
    best_end = start

    for _name, fn in CLAUSES:
        stream.reset(start)
        try:
            effect = fn(stream)
        except Exception:  # noqa: BLE001
            # A clause that raises looks exactly like a clause that declined,
            # which hid a NameError in a brand-new rule behind "this card does
            # not parse". Counted so a diagnostic can say so, and re-raised
            # when the caller asks - tests do.
            CLAUSE_ERRORS[_name] += 1
            if STRICT_CLAUSES:
                raise
            continue
        if effect is None:
            continue
        if stream.mark() > best_end:
            best, best_end = effect, stream.mark()

    stream.reset(best_end if best is not None else start)
    return best


def parse_effects(stream: Stream) -> list[Effect] | None:
    """Every effect in a sentence, in order.

    Returns ``None`` if anything is left unconsumed. That is the
    full-consumption rule, and it is the single most important line in the
    parser: an ability the grammar only partly understood must not fire at all.

    On failure the cursor is left *where the parser got stuck*, not rewound to
    the start. That distinction is what makes the coverage report a work queue
    rather than a histogram of first words - "1,200 abilities stopped at
    'instead'" names a rule to write; "1,200 stopped at 'Whenever'" names
    nothing.
    """
    start = stream.mark()
    effects = _effect_run(stream, allow_or=False)
    if effects is not None:
        return effects

    # "Sacrifice an artifact *or* discard a card." A sentence whose effects
    # are joined by "or" is a choice between them, not a sequence of them -
    # doing both would be a strictly stronger card. Tried only after the
    # ordinary reading has failed, so no sentence that already parsed changes
    # meaning; and wrapped in a mode choice rather than chained, so the
    # engine picks one.
    stuck = stream.mark()
    if not stream.at("or"):
        # The retry below re-parses the whole sentence, which is the single
        # most expensive thing this module does. It can only ever help when
        # the parse died *on* the conjunction, so that is the only case worth
        # paying for - without this guard the second pass ran on every
        # unreadable sentence in the pool and cost 40% of the parse time.
        stream.reset(stuck)
        return None

    stream.reset(start)
    chosen = _effect_run(stream, allow_or=True)
    if chosen is None:
        stream.blocked = max(stream.blocked, stuck)
        stream.reset(stuck)
        return None
    if len(chosen) < 2:
        return chosen
    return [
        Effect(
            EffectKind.CHOOSE_MODE,
            children=tuple(chosen),
            players=YOU,
            text="one of several",
        )
    ]


def _effect_run(stream: Stream, *, allow_or: bool) -> list[Effect] | None:
    """The body of ``parse_effects``, with and without "or" as a separator."""
    effects: list[Effect] = []
    used_or = False
    while True:
        gap = stream.mark()
        stream.skip_punct(",", ".", ";")
        if stream.done:
            break
        if allow_or and stream.accept("or"):
            used_or = True
        else:
            stream.accept("then", "and")
        stream.skip_punct(",", ".", ";")
        if stream.done:
            break
        stuck = stream.mark()
        effect = parse_effect(stream)
        if effect is None:
            stream.reset(stuck)
            # Where no clause could match, which is the honest failure point.
            # Kept as a maximum because an outer clause may retry from further
            # back and its shallower give-up says less.
            stream.blocked = max(stream.blocked, stuck)
            return None
        same_sentence = not any(
            token.text == "." for token in stream.tokens[gap:stuck]
        )
        # The elided subject is the last one the sentence expressed. An
        # instruction with no player of its own - the NOTHING that "of their
        # choice" reads as, the "put it onto the battlefield" of a search -
        # did not express one.
        spoken = [e for e in effects if e.players is not None]
        if spoken and same_sentence:
            effect = _elided_subject(stream.tokens[stuck], effect, spoken[-1])
            if effect is None:
                stream.reset(stuck)
                stream.blocked = max(stream.blocked, stuck)
                return None
        effects.append(effect)

        # Some clauses modify the effect just read rather than following it.
        # Read as siblings, "draw a card unless that player pays {1}" became
        # "draw a card" *and* something else entirely - which is how Rhystic
        # Study parsed into a cost increase.
        # "under its owner's control", "under your control" - a trailing
        # phrase on any effect that moves something. It had a reader,
        # reachable from exactly one clause, so every other way of moving a
        # permanent failed on it: return, exile-and-return, blink.
        #
        # Read *before* the modifiers, not after: it sits between the effect
        # and its "at the beginning of the next end step", so leaving it for
        # last put it in front of the delayed-trigger reader and every blink
        # in the format failed on its own timing clause.
        _under_control(stream)
        # Some clauses modify the effect just read rather than following it.
        # Read as siblings, "draw a card unless that player pays {1}" became
        # "draw a card" *and* something else entirely - which is how Rhystic
        # Study parsed into a cost increase.
        for modifier in (
            _in_any_order_tail,
            _face_down_tail,
            _arrives_tapped_tail,
            _for_each_tail,
            _unless_tail,
            _as_long_as_tail,
            _during_tail,
            _if_tail,
            _delayed_tail,
        ):
            changed = modifier(stream, effects[-1])
            if changed is not None:
                effects[-1] = changed
        _under_control(stream)
    if not effects:
        return None
    if allow_or and not used_or:
        # Nothing changed, so let the caller keep the ordinary reading.
        return None
    return _bind_x(_attach_mana_restriction(effects))


#: Verbs in the third person that open a clause with no subject of its own:
#: "that player loses 1 life *and mills a card*". An imperative ("mill a
#: card") is the ability's controller; this form is the previous subject.
_THIRD_PERSON_VERBS = frozenset(
    {
        "adds", "creates", "discards", "draws", "gains", "gets", "loses",
        "mills", "pays", "reveals", "sacrifices", "scries", "shuffles",
        "surveils",
    }
)

#: Third-person verbs that move cards, which the sentence then goes on to put
#: somewhere with no subject at all ("searches their library for a basic land
#: card, puts it onto the battlefield tapped"). The engine carries those
#: follow-ups out as the ability's controller, so the land would enter under
#: the wrong player's control (CR 110.2a) - refused rather than half-read.
_THIRD_PERSON_MOVERS = frozenset({"exiles", "puts", "searches"})


def _elided_subject(first, effect: Effect, previous: Effect) -> Effect | None:
    """"That player loses 1 life and discards a card" - the second verb's
    subject is the first's, left out, not the ability's controller.

    The clause grammar reads a subjectless verb as "you", which gave Gibbering
    Descent's discard to the Descent's controller. A targeted subject is not
    copied - that would be a second target - but named as "that player", for
    ``referents`` to bind to the player chosen (CR 608.2c). None refuses
    the sentence.
    """
    if first.kind is not TokenKind.WORD:
        return effect
    word = first.lower
    if word not in _THIRD_PERSON_VERBS and word not in _THIRD_PERSON_MOVERS:
        return effect
    if effect.players is None or effect.players.scope is not PlayerScope.YOU:
        return effect
    subject = previous.players
    if subject is None or subject.scope is PlayerScope.YOU:
        return effect
    if word in _THIRD_PERSON_MOVERS:
        return None
    if previous.is_targeted or subject.scope in (
        PlayerScope.TARGET_PLAYER,
        PlayerScope.TARGET_OPPONENT,
    ):
        # Even an instruction the grammar did not mark as targeting: copied,
        # "target opponent" untargeted is read as every opponent.
        subject = PlayerFilter(PlayerScope.REFERRED_PLAYER)
    return replace(effect, players=subject)


#: Instructions that set a pile aside for "from among them" and "the rest".
PILE_MAKERS = frozenset({EffectKind.LOOK_AT_TOP, EffectKind.MILL})

#: Children that run in a resolution of their own - a reflexive or delayed
#: trigger goes on the stack later (CR 603.7, 603.12), and the pile does not
#: go with it.
_OWN_RESOLUTION = frozenset({EffectKind.REFLEXIVE_TRIGGER, EffectKind.DELAYED_TRIGGER})


def pile_references_ok(effects, have_pile: bool = False, local: bool = False) -> bool:
    """Whether every "from among them" / "the rest" in an ability has a pile.

    The grammar reads those phrases wherever they stand, but they mean
    something only after a look, a reveal or a mill *in the same
    resolution*. Two shapes are refused:

    * a pile reference with no pile before it - including one inside an "if
      you do", which the engine resolves as a separate triggered ability;
    * "the rest" anywhere but alongside the instruction that made the pile.
      "You may put a land from among them onto the battlefield. If you
      don't, put a card from among them into your hand. Put the rest on the
      bottom" reads its last sentence into the "if you don't", where it
      would happen only on one branch - a different card.

    Also refused: "if you do / don't" straight after an optional pile move,
    because the engine decides those by what was remembered, and the look
    already remembered the whole pile.
    """
    previous = None
    for effect in effects:
        if effect.kind in (EffectKind.IF_YOU_DONT, EffectKind.REFLEXIVE_TRIGGER) and (
            previous is not None
            and previous.kind is EffectKind.OPTIONAL
            and any(_uses_pile(node) for node in previous.walk())
        ):
            return False
        if _uses_pile(effect):
            if not (have_pile or local):
                return False
            if _is_the_rest(effect) and not local:
                return False
        if effect.kind is EffectKind.SEQUENCE:
            # A sequence is the same level spelled as one node.
            if not pile_references_ok(effect.children, have_pile, local):
                return False
            if any(child.kind in PILE_MAKERS for child in effect.children):
                local = True
        elif effect.kind is EffectKind.CHOOSE_MODE:
            for mode in effect.children:
                if not pile_references_ok((mode,), have_pile or local):
                    return False
        elif effect.children or effect.otherwise:
            inherited = False if effect.kind in _OWN_RESOLUTION else (have_pile or local)
            for branch in (effect.children, effect.otherwise):
                if not _nested_ok(branch, inherited):
                    return False
        if effect.kind in PILE_MAKERS:
            local = True
        previous = effect
    return True


def _nested_ok(effects, have_pile: bool) -> bool:
    """A nested branch: its pile references may lean on an outer pile, but
    its "the rest" may not - only on a pile made in the same branch."""
    if not effects:
        return True
    for effect in effects:
        if _is_the_rest(effect) and not any(e.kind in PILE_MAKERS for e in effects):
            return False
    return pile_references_ok(effects, have_pile)


def _uses_pile(effect: Effect) -> bool:
    return effect.targets is not None and effect.targets.from_pile


def _is_the_rest(effect: Effect) -> bool:
    spec = effect.targets
    return (
        spec is not None
        and spec.from_pile
        and spec.count is None
        and not spec.up_to
        and spec == ObjectFilter(from_pile=True)
    )


def _attach_mana_restriction(effects: list[Effect]) -> list[Effect]:
    """Fold a "spend this mana only on ..." rider into the mana it restricts.

    The rider is a separate sentence on the card but not a separate effect:
    it narrows what the mana just added may pay for. Left as its own node it
    would be a no-op, and the mana would stay general-purpose - which makes
    every ritual and every narrow ramp source better than it is.
    """
    from ..rules.cr100_game_concepts.cr106_mana import SpendOnlyOn

    rider = next(
        (e for e in effects if e.text.startswith(SPEND_ONLY_MARK)), None
    )
    if rider is None:
        return effects

    out: list[Effect] = []
    for effect in effects:
        if effect is rider:
            continue
        if effect.kind is EffectKind.ADD_MANA:
            effect = replace(
                effect,
                mana_restriction=SpendOnlyOn(
                    key=rider.text[len(SPEND_ONLY_MARK) :], filter=rider.targets
                ),
            )
        out.append(effect)
    return out or effects


def _unless_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "unless <player> pays <cost>" wrapped around ``effect``.

    CR 601.2 shape: the named player may pay; if they decline, the effect
    happens. Dropping the clause turns every tax effect into an unconditional
    one, which is a much stronger card than the one printed.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    if not stream.accept("unless"):
        return None

    payer, _ = parse_player_filter(stream)
    if payer is not None:
        cost = _payment_cost(stream)
        if cost is not None:
            return Effect(
                EffectKind.UNLESS_PAYS,
                players=payer,
                pay_cost=cost,
                children=(effect,),
                text=f"unless {payer} pays {cost}",
            )

    # "unless you control a Forest" and friends: a plain state condition.
    stream.reset(mark)
    stream.accept("unless")
    condition = _condition(stream)
    if condition is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=Condition(
            kind=ConditionKind.NOT, operands=(condition,), text="unless ..."
        ),
        children=(effect,),
        text="unless ...",
    )


def _as_long_as_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "as long as <condition>" wrapped around ``effect``.

    Oracle text puts the condition on either side of the effect - "As long as
    you control a Forest, this gets +1/+1" and "this gets +1/+1 as long as you
    control a Forest" are the same card - so both orders have to be read. Only
    the leading form was, and the trailing one is the commoner of the two.
    """
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept_phrase("as long as"):
        stream.reset(mark)
        return None
    condition = _condition(stream)
    if condition is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=condition,
        children=(effect,),
        # Checked continuously, not once - that is what separates this from an
        # "if" clause, and reading it as one-shot would leave a pump in place
        # after its condition stopped holding.
        duration=Duration.WHILE_SOURCE_PERSISTS,
        text="as long as",
    )


def _delayed_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "at the beginning of the next end step" (CR 603.7).

    "You draw a card *at the beginning of the next turn's upkeep*" is not a
    draw now - it is a delayed trigger that draws later, and reading it as an
    immediate draw makes Arcane Denial a straight two-card draw for its
    controller.
    """
    from .triggers import parse_delayed_when

    mark = stream.mark()
    stream.skip_punct(",")
    trigger = parse_delayed_when(stream)
    if trigger is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.DELAYED_TRIGGER,
        trigger=trigger,
        children=(effect,),
        text="later",
    )


def _during_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "during your turn" / "during each opponent's turn".

    This used to be a consume-and-forget: anything after "during", up to eight
    tokens of it, was swallowed so the sentence would complete. That is not a
    neutral simplification. "Creatures you control get +1/+1 during your turn"
    came out as an unconditional anthem - a strictly better card than the one
    printed, on the axis a goldfishing tool exists to measure.

    So the two phrasings the engine can actually ask about become a condition,
    and every other "during ..." declines. Seedborn Muse's "during each other
    player's untap step" is the shape that declines: the engine has no
    schedule for a continuous effect confined to someone else's step, and an
    ability that says so and does nothing is honest where a permanent untap
    would not be.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("during"):
        stream.reset(mark)
        return None

    yours = Condition(kind=ConditionKind.IS_YOUR_TURN, text="during your turn")
    if stream.accept_phrase("your turn") or stream.accept_phrase(
        "each of your turns"
    ):
        condition = yours
    elif (
        stream.accept_phrase("an opponent's turn")
        or stream.accept_phrase("each opponent's turn")
        or stream.accept_phrase("your opponents' turns")
        or stream.accept_phrase("each other player's turn")
    ):
        condition = Condition(
            kind=ConditionKind.NOT,
            operands=(yours,),
            text="during an opponent's turn",
        )
    else:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.CONDITIONAL,
        condition=condition,
        children=(effect,),
        # Checked continuously, exactly as a trailing "as long as" is: the
        # turn changes under a continuous effect that outlives it.
        duration=Duration.WHILE_SOURCE_PERSISTS,
        text=condition.text,
    )


def _for_each_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "for each <filter>" multiplying whatever the effect counted.

    One operator rather than one rule per card. "Deals 1 damage for each
    creature you control", "add {B} for each Swamp", "you gain 2 life for each
    charge counter" are the same construct in three different clauses, and it
    sits at the end of the sentence rather than beside the number - which is
    why attaching it to the amount reader did not work.
    """
    from dataclasses import replace as _replace

    from .nouns import parse_for_each

    mark = stream.mark()
    multiplier = parse_for_each(stream)
    if multiplier is None:
        stream.reset(mark)
        return None

    if effect.kind is EffectKind.ADD_MANA and effect.mana_produced:
        # Mana is produced from a symbol list, so a scaled *amount* would be
        # ignored: "Add {B} for each Swamp you control" would make one black
        # mana however many Swamps were out. The repeat count goes in
        # ``amount2``, which ADD_MANA does not otherwise use.
        return _replace(effect, amount2=multiplier)

    if effect.kind in (EffectKind.NOTHING, EffectKind.SACRIFICE):
        # Neither reads ``amount``: a sacrifice counts in its filter, and a
        # consumed "of their choice" does nothing at all. "Sacrifices a
        # creature of their choice for each black creature you control"
        # multiplied the words "of their choice" and sacrificed one.
        stream.reset(mark)
        return None

    scaled = _multiply(effect.amount, multiplier)
    if scaled is None:
        stream.reset(mark)
        return None

    if effect.kind is EffectKind.MODIFY_PT and not (
        effect.amount2.kind is ValueKind.CONSTANT and effect.amount2.constant == 0
    ):
        # "+1/+1 for each creature you control" scales both halves. Scaling
        # only the power made the toughness half a flat +1, which is a
        # different card and a quietly plausible one.
        return _replace(
            effect, amount=scaled, amount2=_multiply(effect.amount2, multiplier)
        )
    return _replace(effect, amount=scaled)


def _multiply(amount: Value, multiplier: Value) -> Value | None:
    """``amount`` times ``multiplier``, simplified where it is obvious."""
    if amount.kind is ValueKind.CONSTANT:
        if amount.constant in (0, 1):
            # "for each creature you control" with no leading number, and the
            # zero case where the clause never read an amount at all.
            return multiplier
        return Value(
            kind=ValueKind.PRODUCT,
            operands=(amount, multiplier),
        )
    return Value(kind=ValueKind.PRODUCT, operands=(amount, multiplier))


def _if_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "if <condition>" wrapped around ``effect``.

    The same shape as ``unless`` and ``as long as``, and commoner than both -
    but only the leading form was read, so "draw a card if you control a
    Forest" failed on the word "if" while "if you control a Forest, draw a
    card" parsed perfectly.

    Checked once, on resolution (CR 603.4 governs the intervening-if form,
    which the trigger parser handles separately).
    """
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("if"):
        stream.reset(mark)
        return None

    condition = _condition(stream)
    if condition is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=condition,
        children=(effect,),
        text="if ...",
    )


class CostKindName:
    """The name of a CostKind, resolved late.

    The table below is module-level and ``costs`` is imported inside the
    functions that need it, to keep the parser importable without the engine.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _payment_cost(stream: Stream):
    """The "pays {2}" / "pays 3 life" / "sacrifices a creature" half.

    Read as a *cost* rather than as an effect, because that is what it is: the
    player pays it or the effect happens. Reading it as an effect is how
    "pays {1}" turned into a cost-modification clause - the parser found a
    grammar rule that fitted the words and produced a completely different
    card.
    """
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost, UnknownManaSymbol
    from ..rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind

    mark = stream.mark()
    if not stream.accept("pays", "pay"):
        # Not every cost is paid with the word "pay": "unless you sacrifice a
        # land" is a cost too, and returning here meant every upkeep-or-
        # sacrifice permanent failed to parse and then sat on the battlefield
        # for free.
        stream.reset(mark)
        return _action_cost(stream)

    symbols: list[str] = []
    while stream.peek().kind is TokenKind.SYMBOL:
        symbols.append(stream.next().text)
    if symbols:
        try:
            mana = ManaCost.parse("".join(symbols))
        except UnknownManaSymbol:
            stream.reset(mark)
            return None
        # "{2} for each creature they control that's attacking you" - the
        # tax is per attacker, and a flat one is a much weaker card.
        # The shared reader, so a tax scales by anything an effect can scale
        # by - including "for each of those creatures", which the local copy
        # of this logic could not read.
        from .nouns import parse_for_each

        scale = parse_for_each(stream)
        return Cost(
            (CostComponent(CostKind.MANA, mana=mana, scale=scale),)
        )

    amount = stream.accept_number()
    if amount is not None and stream.accept("life"):
        return Cost(
            (CostComponent(CostKind.PAY_LIFE, amount=Value.of(amount), text="pay life"),)
        )

    stream.reset(mark)
    return None


#: The verbs that can stand as a cost, and what paying them consumes.
_ACTION_COSTS = {
    "sacrifice": CostKindName("SACRIFICE"),
    "sacrifices": CostKindName("SACRIFICE"),
    "discard": CostKindName("DISCARD"),
    "discards": CostKindName("DISCARD"),
    "exile": CostKindName("EXILE_FROM_GRAVEYARD"),
    "exiles": CostKindName("EXILE_FROM_GRAVEYARD"),
    "mill": CostKindName("MILL"),
    "mills": CostKindName("MILL"),
}


def _either_cost(stream: Stream):
    """"discard a card or pay 3 life", "sacrifice an artifact or discard a card".

    An additional cost written as a choice (CR 601.2b). Read as the cheaper
    of the two is wrong and reading only the first is wrong; both are kept
    and the payment step picks whichever can be paid.
    """
    first = _payment_cost(stream)
    if first is None:
        return None
    look = stream.mark()
    if not stream.accept("or"):
        return first
    second = _payment_cost(stream)
    if second is None:
        stream.reset(look)
        return first
    from ..rules.cr100_game_concepts.cr118_costs import Cost

    return Cost(choices=(first, second))


def _action_cost(stream: Stream):
    """"unless you sacrifice a land", "unless that player discards a card".

    A cost need not be mana. This reader claimed in its own docstring to
    handle "sacrifices a creature" and in fact handled only mana and life, so
    every upkeep-or-sacrifice permanent in the format failed to parse and
    then sat on the battlefield for free.
    """
    from ..rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind

    mark = stream.mark()
    word = stream.peek().lower
    if word not in _ACTION_COSTS:
        stream.reset(mark)
        return None
    stream.next()

    spec = parse_object_filter(stream)
    if spec is None or chosen_count(spec):
        stream.reset(mark)
        return None

    kind = getattr(CostKind, _ACTION_COSTS[word].name)
    return Cost(
        (
            CostComponent(
                kind,
                filter=spec,
                amount=spec.count if spec.count is not None else Value.of(1),
                text=f"{word} {spec}",
            ),
        )
    )


#: How a "where X is ..." definition is recognised once parsed.
_X_DEFINITION = "where X is"


def _bind_x(effects: list[Effect]) -> list[Effect]:
    """Replace X with its trailing definition (CR 107.3b).

    "Draw X cards, where X is the number of creatures you control" defines X
    *after* using it. The definition arrives as its own effect carrying the
    value; this walks back over the sentence, substitutes, and drops the
    definition, which does nothing on its own.
    """
    definition = next(
        (
            effect.amount
            for effect in effects
            if effect.kind is EffectKind.NOTHING
            and effect.text.startswith(_X_DEFINITION)
        ),
        None,
    )
    if definition is None:
        return effects
    bound = [
        _substitute_x(effect, definition)
        for effect in effects
        if not (
            effect.kind is EffectKind.NOTHING
            and effect.text.startswith(_X_DEFINITION)
        )
    ]
    if any(_mentions_x(effect) for effect in bound):
        # An X the definition could not reach ("unless that player pays
        # {X}"): paying the announced X - zero - is not what the card says.
        return [Effect(EffectKind.UNPARSED, text="where X is (unbound X)")]
    return bound


def _substitute_x(effect: Effect, value: Value) -> Effect:
    """Every X the effect uses, wherever it sits (CR 107.3b).

    Not only the top-level amounts: "-X/-X" is arithmetic with X as an
    operand, "mana value X or less" is a bound inside a filter, "an X/X
    token" is the token's power and toughness. Substituting only the amounts
    left all of those at the announced X - zero for a spell without X in its
    cost - so the card parsed and did nothing.

    An X that cannot be reached this way - a mana symbol, "pays {X}" - is left
    as it is, and ``_bind_x`` refuses the sentence rather than pay {0}.
    """
    return _replace_x(effect, value)


#: Fields that hold abilities of their own - a granted ability or a token's
#: text - whose X, if any, is theirs rather than this sentence's.
_OWN_X_FIELDS = frozenset({"granted_abilities", "abilities", "trigger"})


def _replace_x(node, value: Value):
    """``node`` with each ``Value`` of kind X replaced, rebuilt only where
    something changed."""
    import dataclasses

    if isinstance(node, Value) and node.kind is ValueKind.X:
        return value
    if isinstance(node, tuple):
        rebuilt = tuple(_replace_x(item, value) for item in node)
        return rebuilt if any(a is not b for a, b in zip(rebuilt, node)) else node
    if not dataclasses.is_dataclass(node) or isinstance(node, type):
        return node
    changes = {}
    for field in dataclasses.fields(node):
        if field.name in _OWN_X_FIELDS:
            continue
        current = getattr(node, field.name)
        if not isinstance(current, tuple) and not dataclasses.is_dataclass(current):
            continue
        rebuilt = _replace_x(current, value)
        if rebuilt is not current:
            changes[field.name] = rebuilt
    return dataclasses.replace(node, **changes) if changes else node


def _mentions_x(node) -> bool:
    """Whether an X is still in the sentence after substitution - a value of
    kind X, or an {X} mana symbol, which a value cannot replace."""
    import dataclasses

    if isinstance(node, Value) and node.kind is ValueKind.X:
        return True
    if getattr(node, "text", None) == "{X}":
        return True
    if isinstance(node, tuple):
        return any(_mentions_x(item) for item in node)
    if not dataclasses.is_dataclass(node) or isinstance(node, type):
        return False
    return any(
        _mentions_x(getattr(node, field.name))
        for field in dataclasses.fields(node)
        if field.name not in _OWN_X_FIELDS
    )


# ---------------------------------------------------------------------------
# Cards and library
# ---------------------------------------------------------------------------


@clause("draw")
def _draw(stream: Stream) -> Effect | None:
    """"Draw a card.", "Target player draws two cards.", "Each player draws."."""
    players, targeted = parse_player_filter(stream)
    if not stream.accept("draw", "draws"):
        return None
    # "draws *an additional* card" - the adjective says the draw is on top of
    # a draw that already happened, which changes nothing about this one.
    stream.accept("an", "a")
    stream.accept("additional")
    amount = parse_value(stream) or Value.of(1)
    if not stream.accept("card", "cards"):
        return None
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.DRAW,
        players=players or YOU,
        amount=amount,
        is_targeted=targeted,
        text="draw",
    )


@clause("discard")
def _discard(stream: Stream) -> Effect | None:
    players, targeted = parse_player_filter(stream)
    if not stream.accept("discard", "discards"):
        return None
    amount = parse_value(stream) or Value.of(1)
    stream.accept("card", "cards")
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.DISCARD,
        players=players or YOU,
        amount=amount,
        is_targeted=targeted,
        text="discard",
    )


@clause("mill")
def _mill(stream: Stream) -> Effect | None:
    players, targeted = parse_player_filter(stream)
    if not stream.accept("mill", "mills"):
        return None
    amount = parse_value(stream) or Value.of(1)
    stream.accept("card", "cards")
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.MILL,
        players=players or YOU,
        amount=amount,
        is_targeted=targeted,
        text="mill",
    )


@clause("scry-surveil")
def _scry(stream: Stream) -> Effect | None:
    """CR 701.18 and 701.43: the same shape, different destination."""
    if stream.accept("scry"):
        kind = EffectKind.SCRY
    elif stream.accept("surveil"):
        kind = EffectKind.SURVEIL
    else:
        return None
    amount = parse_value(stream) or Value.of(1)
    return Effect(kind, players=YOU, amount=amount, text="scry/surveil")


@clause("shuffle")
def _shuffle(stream: Stream) -> Effect | None:
    players, _ = parse_player_filter(stream)
    if not stream.accept("shuffle", "shuffles"):
        return None
    stream.accept("your", "their")
    stream.accept("library", "libraries")
    return Effect(EffectKind.SHUFFLE, players=players or YOU, text="shuffle")


# ---------------------------------------------------------------------------
# Life and damage
# ---------------------------------------------------------------------------


@clause("damage")
def _damage(stream: Stream) -> Effect | None:
    """"[Source] deals N damage to [target]."."""
    mark = stream.mark()
    # Who deals it. "This creature deals ..." is the common case, but any
    # noun phrase can be the dealer - "Each creature deals 1 damage to its
    # controller", "Target creature you control deals damage equal to its
    # power" - and reading only the source form failed all of them.
    dealer = None
    look = stream.mark()
    if stream.accept("this"):
        stream.accept(*SELF_NOUNS, "source")
    else:
        dealer, _dealer_targeted = parse_target(stream)
        if dealer is None:
            stream.reset(look)
    if stream.accept_phrase("it deals"):
        dealer, _dealer_targeted = ObjectFilter(remembered=True), False
    elif not stream.accept("deals", "deal"):
        stream.reset(mark)
        return None
    if dealer is not None:
        # The dealer is part of the event (CR 120.1): its lifelink and
        # deathtouch apply, and "damage dealt by" watches it. Only a dealer
        # the engine can name is kept - the source, or the object the
        # resolution is talking about. A *targeted* dealer ("target
        # creature you control deals damage equal to its power") needs a
        # target of its own, and any other dealer ("each creature deals 1
        # damage to its controller") a per-object loop; both were read as
        # the spell dealing the damage, so they stay unread instead.
        if dealer.source_only:
            dealer = None
        elif _dealer_targeted or not dealer.remembered:
            return None

    amount = parse_value(stream)
    if not stream.accept("damage"):
        return None
    # "deals damage *equal to its power*" - the amount after the noun, which
    # every other counted clause reads and this one did not, so the whole
    # fight-and-ping family failed.
    if amount is None:
        amount = _scaled(stream, Value.of(1))
        if amount.is_constant and amount.constant == 1:
            amount = None
    if not stream.accept("to"):
        return None

    # CR 115.4: "any target" is a creature, player, planeswalker or battle.
    if stream.accept_phrase("any target"):
        after = parse_value(stream) if amount is None else None
        return Effect(
            EffectKind.DAMAGE,
            targets=replace(ANY_TARGET),
            amount=amount or after or Value.of(1),
            is_targeted=True,
            damage_source=dealer,
            text="deal damage to any target",
        )

    targets, targeted = parse_target(stream)
    if targets is not None:
        # "to each other creature *and each opponent*" - one damage event
        # with recipients on both sides of the object/player line, which the
        # filter carries as a flag (CR 115.4).
        look = stream.mark()
        if stream.accept("and"):
            also, _ = parse_player_filter(stream)
            if also is not None:
                targets = replace(targets, includes_players=True)
            else:
                stream.reset(look)
        else:
            stream.reset(look)
        return Effect(
            EffectKind.DAMAGE,
            targets=targets,
            amount=_scaled(stream, amount or Value.of(1)),
            is_targeted=targeted,
            damage_source=dealer,
            text="deal damage",
        )
    players, player_targeted = parse_player_filter(stream)
    if players is None:
        return None
    return Effect(
        EffectKind.DAMAGE,
        players=players,
        amount=_scaled(stream, amount or Value.of(1)),
        is_targeted=player_targeted,
        damage_source=dealer,
        text="deal damage",
    )


def _life_amount(stream: Stream) -> Value | None:
    """The amount in a life clause, which sits on either side of "life".

    "gain 3 life" puts it before; "gain life equal to its toughness" puts it
    after. Both forms are common enough that handling only one costs hundreds
    of cards.
    """
    amount = parse_value(stream)
    if amount is not None:
        return amount if stream.accept("life") else None
    if not stream.accept("life"):
        return None
    return parse_value(stream)


@clause("gain-life")
def _gain_life(stream: Stream) -> Effect | None:
    players, targeted = parse_player_filter(stream)
    if not stream.accept("gain", "gains"):
        return None
    amount = _life_amount(stream)
    if amount is None:
        return None
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.GAIN_LIFE,
        players=players or YOU,
        amount=amount,
        is_targeted=targeted,
        text="gain life",
    )


@clause("lose-life")
def _lose_life(stream: Stream) -> Effect | None:
    players, targeted = parse_player_filter(stream)
    if not stream.accept("lose", "loses"):
        return None
    amount = _life_amount(stream)
    if amount is None:
        return None
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.LOSE_LIFE,
        players=players or YOU,
        amount=amount,
        is_targeted=targeted,
        text="lose life",
    )


@clause("take-initiative")
def _take_initiative(stream: Stream) -> Effect | None:
    """"You take the initiative." (CR 726). What taking it sets off - the
    venture into Undercity - is the rules' own triggered ability, not part
    of the card's effect."""
    players, targeted = parse_player_filter(stream)
    if not stream.accept("take", "takes"):
        return None
    if not stream.accept_phrase("the initiative"):
        return None
    return Effect(
        EffectKind.TAKE_INITIATIVE,
        players=players or YOU,
        is_targeted=targeted,
        text="take the initiative",
    )


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


@clause("destroy")
def _destroy(stream: Stream) -> Effect | None:
    if not stream.accept("destroy", "destroys"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None

    # "Destroy target artifact, target creature, target enchantment, and
    # target land." Four separate choices, so four separate effects: a union
    # filter with a count of four would let all four be artifacts.
    extra = _more_targets(stream)
    first = Effect(
        EffectKind.DESTROY, targets=targets, is_targeted=targeted, text="destroy"
    )
    if not extra:
        return first
    return Effect(
        EffectKind.SEQUENCE,
        children=(
            first,
            *(
                Effect(
                    EffectKind.DESTROY,
                    targets=spec,
                    is_targeted=flag,
                    text="destroy",
                )
                for spec, flag in extra
            ),
        ),
        text="destroy several separate targets",
    )


@clause("exile")
def _exile(stream: Stream) -> Effect | None:
    mark = stream.mark()
    # "*Each player* exiles all creature cards from their graveyard" - the
    # exiler need not be you, and reading it as your own exile pointed the
    # whole effect at the wrong graveyards.
    who, _ = parse_player_filter(stream)
    if not stream.accept("exile", "exiles"):
        stream.reset(mark)
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    # "exile them from your graveyard" - the zone says where they are, which
    # a pronoun noun phrase cannot carry on its own.
    origin = None
    mark = stream.mark()
    if stream.accept("from"):
        origin = parse_zone(stream)
        if origin is None:
            stream.reset(mark)
    return Effect(
        EffectKind.EXILE,
        targets=targets,
        from_zone=origin,
        is_targeted=targeted,
        text="exile",
    )


@clause("sacrifice")
def _sacrifice(stream: Stream) -> Effect | None:
    players, player_targeted = parse_player_filter(stream)
    if not stream.accept("sacrifice", "sacrifices"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None or targeted:
        # Nothing is sacrificed as a target: CR 701.21a lets a player
        # sacrifice only what they control, chosen as the instruction is
        # carried out.
        return None
    return Effect(
        EffectKind.SACRIFICE,
        targets=targets,
        players=players or YOU,
        # "Target player sacrifices a creature": the *player* is the target
        # (CR 115.1), chosen as the spell or ability goes on the stack
        # (CR 601.2c), and the spell fizzles if they are gone (CR 608.2b).
        is_targeted=bool(players is not None and player_targeted),
        text="sacrifice",
    )


@clause("counter")
def _counter_spell(stream: Stream) -> Effect | None:
    if not stream.accept("counter", "counters"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    return Effect(
        EffectKind.COUNTER_SPELL,
        targets=targets,
        is_targeted=targeted,
        text="counter",
    )


@clause("return-to-hand")
def _return_to_hand(stream: Stream) -> Effect | None:
    if not stream.accept("return", "returns"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    # The plural is a different string entirely - the tokenizer keeps a
    # possessive whole, so "owners'" and "owner's" share no prefix - and mass
    # bounce is written in the plural without exception.
    if (
        stream.accept_phrase("to its owner's hand")
        or stream.accept_phrase("to their owner's hand")
        or stream.accept_phrase("to their owners' hands")
        or stream.accept_phrase("to their owners' hand")
        or stream.accept_phrase("to your hand")
    ):
        return Effect(
            EffectKind.RETURN_TO_HAND,
            targets=targets,
            is_targeted=targeted,
            text="return to hand",
        )
    if stream.accept("to"):
        zone = parse_zone(stream)
        if zone is None:
            return None
        kind = (
            EffectKind.PUT_ONTO_BATTLEFIELD
            if zone is Zone.BATTLEFIELD
            else EffectKind.MOVE_ZONE
        )
        return Effect(
            kind,
            targets=targets,
            zone=zone,
            is_targeted=targeted,
            text="return",
        )
    return None


# ---------------------------------------------------------------------------
# Permanents
# ---------------------------------------------------------------------------


@clause("tap-untap")
def _tap(stream: Stream) -> Effect | None:
    if stream.accept("tap", "taps"):
        kind = EffectKind.TAP
    elif stream.accept("untap", "untaps"):
        kind = EffectKind.UNTAP
    else:
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    return Effect(kind, targets=targets, is_targeted=targeted, text="tap/untap")


@clause("add-counters")
def _add_counters(stream: Stream) -> Effect | None:
    """"Put a +1/+1 counter on target creature."."""
    if not stream.accept("put", "puts"):
        return None
    amount = parse_value(stream) or Value.of(1)
    token = stream.peek()
    if token.kind is TokenKind.PT:
        stream.next()
        counter = token.text
    elif token.kind is TokenKind.WORD:
        stream.next()
        counter = token.lower
    else:
        return None
    if not stream.accept("counter", "counters"):
        return None
    if not stream.accept("on"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    if stream.accept_phrase("equal to"):
        # "put a number of +1/+1 counters on it equal to its power" - the
        # count trails the target rather than preceding the counter.
        trailing = parse_value(stream)
        if trailing is None:
            return None
        amount = trailing
    amount = _scaled(stream, amount)
    return Effect(
        EffectKind.ADD_COUNTERS,
        targets=targets,
        counter_type=counter,
        amount=amount,
        is_targeted=targeted,
        text="add counters",
    )


@clause("remove-counters")
def _remove_counters(stream: Stream) -> Effect | None:
    """"Remove a +1/+1 counter from target creature."

    The mirror of ``_add_counters``, and it simply did not exist: putting a
    counter on something parsed and taking one off did not, although
    ``REMOVE_COUNTERS`` has had an executor all along. Every -1/-1 counter
    payoff, every vanishing and suspend rider written out in full, and every
    "remove a charge counter" engine failed on a sentence whose twin the
    grammar already read.

    "Remove all X counters" is deliberately not read: the executor takes a
    count, and the count of counters actually there is a question about the
    game state rather than about the card.
    """
    if not stream.accept("remove", "removes"):
        return None
    amount = parse_value(stream)
    if amount is None:
        return None
    counter = _counter_word(stream)
    if counter is None:
        return None
    if not stream.accept("from"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    return Effect(
        EffectKind.REMOVE_COUNTERS,
        targets=targets,
        counter_type=counter,
        amount=amount,
        is_targeted=targeted,
        text="remove counters",
    )


@clause("pump")
def _pump(stream: Stream) -> Effect | None:
    """"Target creature gets +2/+2 until end of turn."."""
    targets, targeted = parse_target(stream)
    if targets is None:
        targets, targeted = SELF, False
        if not stream.accept("this"):
            return None
        stream.accept("creature", "permanent")
    if not stream.accept("gets", "get"):
        return None
    token = stream.peek()
    if token.kind is not TokenKind.PT:
        return None
    stream.next()
    power, _, toughness = token.text.partition("/")
    power_value, toughness_value = _pt_value(power), _pt_value(toughness)

    # "gets +2/+2 and has trample and lifelink" is one sentence granting two
    # different things in two different layers. Reading only the P/T half and
    # failing on the rest cost more abilities than any other single gap.
    granted = _also_gains(stream)
    lost = _also_loses(stream)
    duration = _duration(stream)

    pump = Effect(
        EffectKind.MODIFY_PT,
        targets=targets,
        amount=power_value,
        amount2=toughness_value,
        duration=duration,
        is_targeted=targeted,
        text=f"gets {token.text}",
    )
    if not granted and not lost:
        return pump
    parts = [pump]
    if granted:
        parts.append(
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=targets,
                granted_abilities=granted,
                duration=duration,
                is_targeted=targeted,
                text="and has " + ", ".join(a.text for a in granted),
            )
        )
    if lost:
        # Layer 6 removal alongside a layer 7 change, in one sentence.
        parts.append(
            Effect(
                EffectKind.REMOVE_ABILITIES,
                targets=targets,
                keywords=lost,
                duration=duration,
                is_targeted=targeted,
                text="and loses " + ", ".join(lost),
            )
        )
    return Effect(
        EffectKind.SEQUENCE,
        children=tuple(parts),
        text="gets P/T and gains abilities",
    )


def _all_creature_types(stream: Stream) -> bool:
    """"gains all creature types" - changeling, written out (CR 702.73a)."""
    return bool(
        stream.accept_phrase("all creature types")
        or stream.accept_phrase("every creature type")
    )


def _also_loses(stream: Stream) -> tuple:
    """A trailing "and loses flying" on a pump clause (layer 6, removal).

    The mirror of ``_also_gains``, and the reason Sword-style "gets +10/+10
    and loses flying" failed: the sentence grants and removes at once, and
    only the granting half had a reader.
    """
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("and"):
        stream.reset(mark)
        return ()
    if not stream.accept("loses", "lose"):
        stream.reset(mark)
        return ()

    if stream.accept_phrase("all abilities"):
        return ("*",)
    granted = _quoted_run(stream)
    if not granted:
        stream.reset(mark)
        return ()
    return tuple(a.keyword or a.text for a in granted)


def _also_gains(stream: Stream) -> tuple:
    """A trailing "and has flying and trample" on a pump clause."""
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("and"):
        stream.reset(mark)
        return ()
    if not stream.accept("has", "have", "gains", "gain"):
        stream.reset(mark)
        return ()

    # The same reader the grant clause uses, so "gets +2/+2 and has protection
    # from black" works in either order. This half used to have its own,
    # simpler list reader that knew nothing of quotations or of keywords that
    # take an argument, so the two orders disagreed about what a keyword is.
    granted = _quoted_run(stream)
    if granted:
        return granted
    # "have base power and toughness X/X *and gain all creature types*" -
    # changeling by another name, which a keyword run cannot express because
    # there is no keyword in the sentence.
    if _all_creature_types(stream):
        from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind

        return (
            Ability(AbilityKind.STATIC, keyword="Changeling", text="changeling"),
        )
    return _reset_empty(stream, mark)


def _reset_empty(stream: Stream, mark: int) -> tuple:
    stream.reset(mark)
    return ()


def _pt_value(text: str) -> Value:
    text = text.strip()
    if text in ("X", "+X"):
        return Value(kind=ValueKind.X)
    if text == "-X":
        return Value(
            kind=ValueKind.DIFFERENCE,
            operands=(Value.of(0), Value(kind=ValueKind.X)),
        )
    try:
        return Value.of(int(text))
    except ValueError:
        return Value.of(0)


def _quoted_run(stream: Stream) -> tuple:
    """A comma/and separated run of quoted abilities and bare keywords.

    Cards mix the two freely - "gains flying and '{T}: Draw a card'" - so one
    reader handles both. An unreadable quotation returns nothing at all rather
    than a partial list, because granting some of what a card says is a
    quieter kind of wrong than granting none of it.
    """
    from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
    from ..rules.cr700_additional_rules.keywords import is_known

    granted: list = []
    while True:
        # "protection from black", "ward {2}", "protection from each color" -
        # keywords that take an argument. The run reader only knew bare names,
        # so a list stopped dead at the first one that carried a parameter.
        parameterised = _keyword_with_argument(stream)
        if parameterised == ():
            # A parameterised keyword whose argument cannot be read: the whole
            # grant fails rather than granting the keyword unqualified.
            return ()
        if parameterised is not None:
            granted.extend(parameterised)
        elif stream.peek().text == '"':
            inner = _read_quotation(stream)
            if inner is None:
                return ()
            granted.append(inner)
        else:
            token = stream.peek()
            if token.kind is not TokenKind.WORD:
                break
            pair = f"{token.text} {stream.peek(1).text}".strip()
            if is_known(pair):
                stream.next()
                stream.next()
                name = pair
            elif is_known(token.text):
                stream.next()
                name = token.text
            else:
                break
            built = _granted_keyword(name, stream)
            if built is None:
                return ()
            granted.extend(built)
        if not _another_keyword_follows(stream):
            break
    return tuple(granted)


def _granted_keyword(name: str, stream: Stream):
    """A keyword named in a grant, expanded exactly as a printed one would be.

    "Creatures you control have prowess" grants prowess - the triggered
    ability CR 702.108a defines - not an inert ability that happens to be
    called Prowess. Granted keywords used to be built as a bare name, which is
    right for flying (the engine asks for it by name) and silently wrong for
    every keyword that is really a trigger or carries a quality: granted
    prowess, exalted, annihilator 2 and swampwalk all did nothing while the
    card read as understood. So the keyword registry expands it, and the grant
    fails when the registry's expansion is itself not understood - a keyword
    the engine cannot run is not granted as if it could.

    A numeric argument ("afflict 3", "toxic 1") or a mana cost ("ninjutsu
    {2}{U}{B}") is read here; nothing else is. Where the expansion is just the
    named static ability, the bare form is kept, which is the same ability.
    """
    from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
    from ..rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
    from .compile import understood

    amount = 0
    cost = None
    look = stream.mark()
    number = stream.accept_number()
    if number is not None:
        # "mobilize X, where X is ..." is not read here; a plain number is.
        amount = number
    elif stream.peek().kind is TokenKind.SYMBOL:
        from ..rules.cr100_game_concepts.cr118_costs import Cost

        symbols = _mana_run(stream)
        if not symbols:
            stream.reset(look)
            return None
        cost = Cost.mana("".join(symbols))
    elif stream.peek().text == "X":
        return None

    built = build(KeywordInstance(name, amount=amount, cost=cost, text=name))
    if not built or not all(understood(ability) for ability in built):
        stream.reset(look)
        return None
    if (
        len(built) == 1
        and amount == 0
        and cost is None
        and built[0].kind is AbilityKind.STATIC
        and not built[0].effects
        and built[0].quality is None
        and built[0].trigger is None
    ):
        return (Ability(AbilityKind.STATIC, keyword=name, text=name),)
    return built


def _another_keyword_follows(stream: Stream) -> bool:
    """Step over the separator between two granted abilities, if there is one.

    Only when another ability actually follows. "gain trample *and* get
    +3/+3" ends its keyword list at trample, and swallowing that "and" left
    the pump half of the sentence with no conjunction to recognise itself by.
    The separator may be a comma, a conjunction, or both.
    """
    look = stream.mark()
    stream.skip_punct(",")
    stream.accept("and", "or")
    if stream.mark() != look and _starts_a_keyword(stream):
        return True
    stream.reset(look)
    return False


def _starts_a_keyword(stream: Stream) -> bool:
    """Whether the next token begins another granted ability."""
    from ..rules.cr700_additional_rules.keywords import is_known

    token = stream.peek()
    if token.text == '"':
        return True
    if token.lower in _PARAMETERISED_KEYWORDS:
        return True
    if token.kind is not TokenKind.WORD:
        return False
    pair = f"{token.text} {stream.peek(1).text}".strip()
    return is_known(token.text) or is_known(pair)


#: Keywords whose name is followed by something that belongs to it.
_PARAMETERISED_KEYWORDS = ("protection", "ward", "hexproof", "landwalk")


def _keyword_with_argument(stream: Stream):
    """"protection from black and from green", "ward {2}", "hexproof from blue".

    Returns the abilities the phrase grants; ``None`` when the next words are
    not one of these keywords; and ``()`` when they are but the argument
    cannot be read, which fails the grant.

    CR 702.16g: "protection from black and from green" is two protection
    abilities, one per quality. The quality is the whole point of the keyword.
    Read as a bare "Protection", which is what this used to produce, every
    Sword granted protection from *everything*; and the argument reader ate
    up to fourteen words, so "gains protection from the color of your choice
    until end of turn" lost its duration as well, and Mother of Runes handed
    out permanent protection from everything.
    """
    from ..rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
    from .compile import understood

    mark = stream.mark()
    word = stream.peek().lower
    if word not in _PARAMETERISED_KEYWORDS:
        return None
    stream.next()

    # "ward {2}". Built by the registry, so a granted ward is the same ability
    # as a printed one - and, while the registry's ward cannot collect its
    # payment, not understood either. It used to be a bare "Ward" that did
    # nothing and counted as read.
    if word == "ward":
        from ..rules.cr100_game_concepts.cr118_costs import Cost

        symbols = _mana_run(stream)
        if not symbols:
            stream.reset(mark)
            return ()
        built = build(
            KeywordInstance("Ward", cost=Cost.mana("".join(symbols)), text="Ward")
        )
        if not all(understood(ability) for ability in built):
            stream.reset(mark)
            return ()
        return built

    if word == "landwalk" or not stream.at("from"):
        stream.reset(mark)
        return None
    qualities = _protection_qualities(stream)
    if qualities is None:
        stream.reset(mark)
        return ()
    name = "Protection" if word == "protection" else "Hexproof from"
    out: list = []
    for quality in qualities:
        out.extend(build(KeywordInstance(name, filter=quality, text=name)))
    return tuple(out)


#: The colour words a protection quality can name (CR 105.1).
_COLOUR_QUALITIES = ("white", "blue", "black", "red", "green")

#: "Protection from everything" (CR 702.16j): the keyword with no quality,
#: which ``protected_from`` treats as matching any source.
_EVERYTHING = object()


def _protection_qualities(stream: Stream):
    """"from red", "from red and from blue", "from everything", "from Humans".

    One filter per quality, ``None`` standing for everything, or ``None``
    overall for a quality that cannot be expressed. The filters carry no zone,
    because a quality is asked of a source wherever it is - a spell on the
    stack, an ability's source, an attacking creature.

    Declined: "the color of your choice" and "the chosen color", which need a
    choice this reader cannot make; qualities that name players ("from your
    opponents"); and any noun phrase narrowing more than type and subtype.
    """
    qualities: list = []
    while True:
        if not stream.accept("from"):
            return None
        quality = _one_quality(stream)
        if quality is None:
            return None
        qualities.append(None if quality is _EVERYTHING else quality)
        look = stream.mark()
        stream.skip_punct(",")
        stream.accept("and")
        if stream.at("from"):
            continue
        stream.reset(look)
        return qualities


def _one_quality(stream: Stream):
    from ..rules.kernel.enums import Color

    word = stream.peek().lower
    if word == "everything":
        stream.next()
        return _EVERYTHING
    if word in _COLOUR_QUALITIES:
        stream.next()
        return ObjectFilter(colors_any=Color[word.upper()], zones=frozenset())
    if word == "multicolored":
        stream.next()
        return ObjectFilter(must_be_multicolored=True, zones=frozenset())
    if word == "monocolored":
        stream.next()
        return ObjectFilter(must_be_monocolored=True, zones=frozenset())
    if word == "colorless":
        stream.next()
        return ObjectFilter(must_be_colorless=True, zones=frozenset())
    if stream.accept_phrase("all colors") or stream.accept_phrase("each color"):
        # "Protection from all colors" is protection from each color: a
        # source is stopped by it exactly when it has at least one.
        if stream.at("that's", "that", "not"):
            return None
        return ObjectFilter(must_be_coloured=True, zones=frozenset())

    look = stream.mark()
    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(look)
        return None
    simple = ObjectFilter(
        types_any=spec.types_any,
        types_all=spec.types_all,
        subtypes_any=spec.subtypes_any,
        zones=spec.zones,
    )
    if spec != simple or not (spec.types_any or spec.types_all or spec.subtypes_any):
        stream.reset(look)
        return None
    return replace(spec, zones=frozenset())


def _read_quotation(stream: Stream):
    """Consume a '"..."' run and parse what is inside it as an ability."""
    from .compile import parse_inner_ability

    mark = stream.mark()
    if not stream.accept('"'):
        return None

    words: list[str] = []
    depth = 0
    while not stream.done:
        token = stream.peek()
        if token.text == '"' and depth == 0:
            stream.next()
            break
        stream.next()
        words.append(token.text)
    else:
        stream.reset(mark)
        return None

    inner = parse_inner_ability(" ".join(words))
    if inner is None:
        stream.reset(mark)
        return None
    return inner


@clause("grant-ability")
def _grant_keyword(stream: Stream) -> Effect | None:
    """"gains flying", "gains '{T}: Add {G}'", and any mixture of the two.

    One clause rather than one per form, because cards combine them in a
    single list and a reader that handles only keywords stops at the first
    quotation mark.
    """
    targets, targeted = parse_target(stream)
    if targets is None:
        if not stream.accept("this"):
            return None
        stream.accept("creature", "permanent")
        targets, targeted = SELF, False
    if not stream.accept("gains", "gain", "has", "have"):
        return None

    granted = _quoted_run(stream)
    if not granted:
        # "gain all creature types" - changeling by another name, and a
        # keyword run cannot express it because there is no keyword.
        if not _all_creature_types(stream):
            return None
        from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind

        granted = (
            Ability(AbilityKind.STATIC, keyword="Changeling", text="changeling"),
        )

    # "gain trample and get +X/+X", "gets +2/+2 and has protection from black"
    # - one sentence granting an ability and changing power in either order.
    # The pump half is read here so the sentence does not have to be split.
    pump = _trailing_pump(stream, targets, targeted)
    duration = _duration(stream)

    grant = Effect(
        EffectKind.GRANT_ABILITY,
        targets=targets,
        granted_abilities=granted,
        duration=duration,
        is_targeted=targeted,
        text="gains " + ", ".join(a.keyword or a.text for a in granted),
    )
    if pump is None:
        return grant
    from dataclasses import replace as _replace

    return Effect(
        EffectKind.SEQUENCE,
        children=(grant, _replace(pump, duration=duration)),
        text="gains abilities and changes power",
    )


def _trailing_pump(stream: Stream, targets, targeted):
    """A trailing "and get +2/+2" on a grant."""
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("and"):
        stream.reset(mark)
        return None
    if not stream.accept("get", "gets"):
        stream.reset(mark)
        return None

    token = stream.peek()
    if token.kind is not TokenKind.PT:
        stream.reset(mark)
        return None
    stream.next()
    left, _, right = token.text.partition("/")
    return Effect(
        EffectKind.MODIFY_PT,
        targets=targets,
        amount=_pt_value(left),
        amount2=_pt_value(right),
        is_targeted=targeted,
        text=f"gets {token.text}",
    )


# ---------------------------------------------------------------------------
# MTG Arena's "perpetually" (see rules/cr700_additional_rules/digital_mechanics)
# ---------------------------------------------------------------------------


@clause("perpetually")
def _perpetually(stream: Stream) -> Effect | None:
    """"Creature cards in your hand perpetually get +1/+1", "It perpetually
    gains flash", "A random land card in your library perpetually gains ...".

    The subject is read here; what happens to it is read by the ordinary
    continuous-effect clauses, applied to "this" - the same words, minus the
    adverb, are a pump or a grant the grammar already knows. The result is
    accepted only if every part is a layered change aimed at that "this" with
    no duration of its own, which is what a perpetual change can be. Anything
    else - "perpetually ... until end of turn", a change the clauses read
    with a target of their own, a "for each" or "as long as" after it - is
    left unread rather than re-aimed.

    Also reads the two-sentence form "Choose a nonland card in your hand. It
    perpetually gains flash." - the choice is the subject.
    """
    mark = stream.mark()
    subject = _perpetual_subject(stream)
    if subject is None:
        stream.reset(mark)
        return None
    targets, targeted, at_random = subject
    if not stream.accept("perpetually"):
        stream.reset(mark)
        return None

    changes = _perpetual_changes(stream)
    if changes is None:
        stream.reset(mark)
        return None
    # "... and perpetually gains 'This spell costs {1} less to cast.'"
    while True:
        look = stream.mark()
        stream.skip_punct(",")
        if not stream.accept_phrase("and perpetually"):
            stream.reset(look)
            break
        more = _perpetual_changes(stream)
        if more is None:
            stream.reset(mark)
            return None
        changes += more

    # A trailing modifier would be applied to the wrapper rather than to the
    # change: "for each" would scale nothing, and "as long as"/"during" would
    # turn a continuous condition into a one-off check.
    look = stream.mark()
    stream.skip_punct(",")
    if stream.at("for", "as", "during", "until", "this"):
        if (
            stream.at_phrase("for each")
            or stream.at_phrase("as long as")
            or stream.at("during", "until")
            or stream.at_phrase("this turn")
        ):
            stream.reset(mark)
            return None
    stream.reset(look)

    return Effect(
        EffectKind.PERPETUALLY,
        targets=targets,
        is_targeted=targeted,
        keywords=("at random",) if at_random else (),
        children=tuple(changes),
        text="perpetually",
    )


#: "The duplicate", "those duplicates": what a conjure just made.
_DUPLICATE_PHRASES = (
    "the duplicates", "the duplicate", "that duplicate", "those duplicates",
)


def _perpetual_subject(stream: Stream):
    """(filter, targeted, at random) for the thing a perpetual change is on."""
    mark = stream.mark()
    for phrase in _DUPLICATE_PHRASES:
        if stream.accept_phrase(phrase):
            return ObjectFilter(remembered=True), False, False

    # "Choose a creature card in your hand. It perpetually gets +1/+1."
    if stream.accept("choose"):
        start = stream.mark()
        if not stream.accept("a", "an"):
            stream.reset(mark)
            return None
        spec = parse_object_filter(stream)
        if spec is None or not _cards_elsewhere(stream, start, spec):
            stream.reset(mark)
            return None
        stream.skip_punct(".")
        if not (
            stream.accept("it")
            or stream.accept_phrase("that card")
            or stream.accept_phrase("that creature card")
        ):
            stream.reset(mark)
            return None
        spec = _owned_zone(stream, start, spec)
        if spec is None:
            stream.reset(mark)
            return None
        return replace(spec, count=Value.of(1), up_to=False), False, False

    # "a random nonland card in your hand"
    if stream.accept("a", "an") and stream.accept("random"):
        start = stream.mark()
        spec = parse_object_filter(stream)
        if spec is None or spec.count is not None or spec.remembered:
            stream.reset(mark)
            return None
        spec = _owned_zone(stream, start, spec)
        if spec is None:
            stream.reset(mark)
            return None
        return replace(spec, count=Value.of(1), up_to=False), False, True
    stream.reset(mark)

    start = stream.mark()
    targets, targeted = parse_target(stream)
    if targets is None or targets.includes_players:
        stream.reset(mark)
        return None
    targets = _owned_zone(stream, start, targets)
    if targets is None:
        stream.reset(mark)
        return None
    return targets, targeted, False


def _cards_elsewhere(stream: Stream, start: int, spec: ObjectFilter) -> bool:
    """A chosen card must be named as a card in a zone - "a creature card in
    your hand" - since "choose a creature" alone would be a permanent."""
    return bool(spec.zones) and Zone.BATTLEFIELD not in spec.zones


def _owned_zone(stream: Stream, start: int, spec: ObjectFilter):
    """Hold "in your hand/graveyard/library" to *your* zone, or decline.

    The noun reader reads "cards in your graveyard" as cards in any graveyard
    - the "your" is dropped (reported; it is not this construct's to fix) -
    and a perpetual change on every player's cards is not the card. So the
    words consumed are checked here: "in your <zone>" pins the owner, and any
    other player's zone ("in that player's hand") is declined outright, as is
    a "permanent card" off the battlefield, which the noun reader reads as a
    card of any type at all.
    """
    from ..rules.kernel.query import ControllerRelation

    words = [token.lower for token in stream.tokens[start : stream.mark()]]
    offboard = spec.zones and Zone.BATTLEFIELD not in spec.zones
    if offboard and any(w in ("permanent", "permanents") for w in words):
        return None
    for index, word in enumerate(words):
        if word != "in" or index + 1 >= len(words):
            continue
        owner_word = words[index + 1]
        if owner_word == "your":
            if spec.owner is ControllerRelation.ANY:
                spec = replace(spec, owner=ControllerRelation.YOU)
        elif owner_word in ("the",):
            continue
        else:
            return None
    return spec


def _perpetual_changes(stream: Stream) -> list[Effect] | None:
    """The change after "perpetually", read as if it were said of "this".

    Returns the layered changes, re-aimed at the object they will be bound to
    (``targets`` None), or ``None`` if what follows is not purely that.
    """
    from ..rules.cr700_additional_rules.digital_mechanics import PERPETUAL_KINDS
    from .tokens import Token

    start = stream.mark()
    this = Token(TokenKind.WORD, "this")
    sub = Stream([this, *stream.tokens[start:]])
    inner = parse_effect(sub)
    if inner is None or sub.mark() <= 1:
        return None

    out: list[Effect] = []
    for node in inner.walk():
        if node.kind is EffectKind.SEQUENCE:
            if node.duration or not node.condition.is_always:
                return None
            continue
        if node.kind not in PERPETUAL_KINDS:
            return None
        if node.duration or node.is_targeted or not node.condition.is_always:
            return None
        if node.text.startswith(CDA_MARK):
            return None
        if node.targets is not None and replace(
            node.targets, zones=ObjectFilter().zones
        ) != SELF:
            return None
        if node.kind is EffectKind.GRANT_ABILITY and any(
            ability.keyword == "Ward" for ability in node.granted_abilities
        ):
            # "gains ward {1}" is read without its cost; granting a Ward that
            # asks for nothing is not the card.
            return None
        out.append(replace(node, targets=None))
    if not out:
        return None
    stream.reset(start + sub.mark() - 1)
    return out


@clause("create-token")
def _create_token(stream: Stream) -> Effect | None:
    """"Create a 1/1 white Soldier creature token."

    The maker is usually you but need not be: "Destroy target permanent. Its
    controller creates a 3/3 green Beast" hands the token to the victim, and
    reading that as your own token turns a drawback into an upside.
    """
    mark = stream.mark()
    maker, _ = parse_player_filter(stream)
    if not stream.accept("create", "creates"):
        stream.reset(mark)
        return None
    amount = parse_value(stream) or Value.of(1)

    token = stream.peek()
    power = toughness = None
    if token.kind is TokenKind.PT:
        stream.next()
        left, _, right = token.text.partition("/")
        power, toughness = _pt_value(left), _pt_value(right)

    spec = parse_object_filter(stream)
    if spec is None:
        return None
    if not stream.accept("token", "tokens") and not spec.is_token:
        # "create a Treasure token" puts the word before the noun sometimes;
        # if it is absent this was not a token clause at all. The noun reader
        # may also have taken the word itself - "1/1 white Soldier creature
        # token" ends in a qualifier it now recognises - in which case the
        # filter says so and there is nothing left here to accept.
        return None

    keywords, abilities = _token_abilities(stream)
    # "... token with flying" is read by the *noun* reader, which takes
    # "with flying" as a constraint and puts it in ``has_keyword``. Nothing
    # then carried it to the token, so every token printed with an ability -
    # every Spirit with flying, every Angel, every deathtouch Snake - was
    # created vanilla, and the sentence parsed perfectly while doing it.
    keywords = tuple(dict.fromkeys(keywords + tuple(spec.has_keyword)))
    # "a token that's a copy of target creature you control, *except the
    # token has flying and it isn't legendary*". The becomes-a-copy clause
    # read this tail and the token-copy clause did not, so the two halves of
    # one construct disagreed.
    _copy_exceptions(stream)

    types = _token_types(spec)
    if types is None:
        return None
    if types & CardType.CREATURE and power is None:
        # A creature token whose power and toughness the sentence never gave.
        # Defaulted to 0/0 it is created and then dies to state-based actions
        # before anything can use it, which is a card that does nothing
        # wearing the costume of one that does.
        return None

    return Effect(
        EffectKind.CREATE_TOKEN,
        token=TokenSpec(
            name=spec.subtypes_all[0] if spec.subtypes_all else "Token",
            types=types,
            subtypes=spec.subtypes_all,
            colors=spec.colors_any,
            power=power or Value.of(0),
            toughness=toughness or Value.of(0),
            keywords=keywords,
            abilities=abilities,
        ),
        players=maker or YOU,
        amount=amount,
        text="create token",
    )


def _token_types(spec: ObjectFilter) -> CardType | None:
    """The card types of a token whose sentence may not have named any.

    "Create a Treasure token" says no card type at all, and defaulting to
    creature made a **0/0 creature** named Treasure that died to state-based
    actions the instant it arrived - for one of the most-played effects in the
    format. The subtype registry already knows what a Treasure is, so it is
    asked rather than guessed at.

    ``None`` means the sentence did not say and nothing can tell, which fails
    the ability. A token of the wrong card type is not a near miss: it is a
    permanent that dies immediately, or one that never dies at all.
    """
    from ..rules.cr200_parts_of_a_card.cr205_typeline import active_registry

    if spec.types_all:
        return spec.types_all
    if len(spec.subtypes_all) != 1:
        return None
    types = active_registry().types_for(spec.subtypes_all[0])
    # More than one card type means the word alone does not decide - a bare
    # subtype that is both a creature type and an artifact type could be
    # either, and the sentence has to say.
    if types is CardType.NONE or types.bit_count() != 1:
        return None
    return types


def _token_abilities(stream: Stream) -> tuple[tuple[str, ...], tuple]:
    """"... token with flying" and "... token with 'Whenever this attacks...'".

    Returns the keyword names and the written-out abilities separately: the
    token spec keeps keywords as names so the keyword registry expands them,
    and anything quoted is already a built ability.
    """
    mark = stream.mark()
    if not stream.accept("with"):
        return (), ()

    granted = _quoted_run(stream)
    if not granted:
        stream.reset(mark)
        return (), ()

    # One keyword can expand to several abilities (CR 702.16g: protection
    # from two colours is two abilities), and the token spec keeps names.
    keywords = tuple(dict.fromkeys(a.keyword for a in granted if a.keyword))
    written = tuple(a for a in granted if not a.keyword)
    return keywords, written


# ---------------------------------------------------------------------------
# Mana
# ---------------------------------------------------------------------------


@clause("add-mana")
def _add_mana(stream: Stream) -> Effect | None:
    """"Add {G}.", "Add {C}{C}.", "Add {R} or {G}.", "its controller adds {G}."."""
    mark = stream.mark()
    # "*Its controller* adds an additional {G}" - the mana need not be yours.
    # Reading it as your own turns a symmetrical land enchantment into a
    # one-sided one.
    maker, _ = parse_player_filter(stream)
    if not stream.accept("add", "adds"):
        stream.reset(mark)
        return None

    # "Add six {G}" and "Add an additional {B}" - a count or an adjective in
    # front of the symbols. The count multiplies them; "additional" is a
    # description of when it happens and changes nothing here.
    repeat = 1
    look = stream.mark()
    count = stream.accept_number()
    if count is not None and stream.peek().kind is TokenKind.SYMBOL:
        repeat = max(1, count)
    else:
        stream.reset(look)
    # "an additional {B}" - "an" tokenizes as the number one, so the count
    # probe above consumes it and then rewinds; the article has to be taken
    # again here before the adjective.
    stream.accept("a", "an", "one")
    stream.accept("additional")

    # "one mana of the chosen color", "four mana of the chosen color" - the
    # colour was picked as the permanent entered and is recorded on it, so
    # there is no symbol here to read.
    chosen = _chosen_colour_mana(stream)
    if chosen is not None:
        produced = _replace_amount(chosen, repeat)
        if maker is not None:
            from dataclasses import replace as _replace2

            produced = _replace2(produced, players=maker)
        return produced

    symbols = _mana_run(stream)
    if not symbols:
        return None
    symbols = symbols * repeat

    alternatives = [symbols]
    chosen_alternative = None
    while True:
        look = stream.mark()
        # "Add {W}, {U}, or {B}" spells the "or" once, before the last item -
        # exactly like a list of card types, and it was failing for exactly
        # the same reason: a conjunction was required at every step.
        comma = stream.peek().text == ","
        if comma:
            stream.skip_punct(",")
        joined = stream.accept("or")
        if not comma and not joined:
            stream.reset(look)
            break
        # CR 106.1b: "Add {R} or {G}" is one mana, chosen on resolution - not
        # two. A parser that produced both effects would hand out double mana
        # from every dual land in the pool.
        more = _mana_run(stream)
        if not more:
            # "Add {U} or one mana of the chosen color" - the second half of
            # the choice names a recorded colour instead of a symbol.
            stream.accept("a", "an", "one")
            chosen = _chosen_colour_mana(stream)
            if chosen is not None:
                chosen_alternative = chosen
                break
            stream.reset(look)
            break
        alternatives.append(more)

    modes = [_mana_effect(group) for group in alternatives]
    if chosen_alternative is not None:
        modes.append(chosen_alternative)
    if any(mode is None for mode in modes):
        return None
    if len(modes) == 1:
        # The "for each" tail is deliberately *not* read here. Mana comes from
        # a symbol list, so scaling the amount would be ignored; the trailing
        # modifier puts the repeat count in amount2, which is what ADD_MANA
        # actually honours.
        single = modes[0]
        if maker is not None:
            from dataclasses import replace as _replace

            return _replace(single, players=maker)
        return single
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=tuple(modes),
        players=YOU,
        text="add one of several",
    )


#: Every way oracle text says "and you pick the colour". They differ in which
#: colours are on the menu - the engine settles that at resolution from the
#: board - so as far as the grammar is concerned they are one construct.
#: Written once, because writing them one per card is how "of any color" and
#: "of any one color" ended up in separate branches that could drift.
#: Kept for the fixed phrases that carry no relative clause. The open-ended
#: "... that <something> could produce" forms are read by rule instead, in
#: ``_could_produce_tail`` - writing them out card by card was always going
#: to be one short, and Exotic Orchard and Fellwar Stone were the two it was
#: short by.
_COLOUR_SOURCES = (
    "of any color",
    "of any colour",
    "of any one color",
    "of any type that a land you control could produce",
    "of any color that a land you control could produce",
    "of any of the exiled card's colors",
    "of any of the exiled cards' colors",
    "in any combination of colors",
    "of the chosen color",
    "of that color",
    "of any color among permanents you control",
)

#: Tails that narrow which colour is chosen without changing how much mana
#: appears. Consumed for the same reason - the choice is the engine's.
_COLOUR_TAILS = (
    "in your commander's color identity",
    "in your commanders' color identity",
    "among colors of permanents you control",
    "that a land you control could produce",
)

def _any_colour():
    """WUBRG. A function rather than a constant because ``Color`` is imported
    inside the mana clauses, not at module scope."""
    from ..rules.kernel.enums import Color

    return Color.WHITE | Color.BLUE | Color.BLACK | Color.RED | Color.GREEN


def _amount_of_mana(stream: Stream) -> Effect | None:
    """"an amount of mana of that color equal to <value>" (Nykthos and family).

    The whole effect, because the quantity and the colour arrive in the
    opposite order from every other mana ability and separating them buys
    nothing.
    """
    from ..rules.kernel.query import YOU

    mark = stream.mark()
    if not stream.accept_phrase("an amount of"):
        stream.reset(mark)
        return None
    # "an amount of *{C}* equal to that spell's mana value" - the symbol
    # names the mana directly instead of describing its colour. Same effect,
    # and only the described form was read.
    symbols = _mana_run(stream)
    if symbols:
        if not stream.accept_phrase("equal to"):
            stream.reset(mark)
            return None
        amount = parse_value(stream)
        if amount is None:
            stream.reset(mark)
            return None
        produced = _mana_effect(symbols)
        if produced is None:
            stream.reset(mark)
            return None
        from dataclasses import replace as _replace

        return _replace(produced, amount=amount, amount2=amount)

    if not stream.accept("mana"):
        stream.reset(mark)
        return None

    # "of that color", "of the chosen color": the one colour the source
    # chose, not a menu - Nykthos makes mana of the colour it chose.
    chosen = (
        stream.accept_phrase("of that color")
        or stream.accept_phrase("of that colour")
        or stream.accept_phrase("of the chosen color")
        or stream.accept_phrase("of the chosen colour")
    )
    colours = None if chosen else _mana_colour_source(stream)
    if colours is None and not chosen:
        stream.reset(mark)
        return None
    if not stream.accept_phrase("equal to"):
        stream.reset(mark)
        return None

    amount = parse_value(stream)
    if amount is None:
        stream.reset(mark)
        return None

    if chosen:
        return Effect(
            EffectKind.ADD_MANA,
            players=YOU,
            amount=amount,
            colors_chosen=True,
            text="add an amount of mana of the chosen colour",
        )
    return Effect(
        EffectKind.ADD_MANA,
        players=YOU,
        amount=amount,
        colors=colours,
        text="add an amount of mana",
    )


def _chosen_colour_mana(stream: Stream):
    """"mana of the chosen color" - the colour this permanent recorded - and
    "mana of any type that land produced", the types the tapping that
    triggered the ability produced (CR 106.12a, 106.1b)."""
    mark = stream.mark()
    stream.accept("mana")
    if stream.accept_phrase("of any type that land produced"):
        # Not "any color": colorless is a type too, and a Forest's tapping
        # offers green alone. Read off the triggering event at resolution.
        return Effect(
            EffectKind.ADD_MANA,
            players=YOU,
            amount=Value.of(1),
            colors_from_trigger=True,
            text="add mana of a type that land produced",
        )
    if not (
        stream.accept_phrase("of the chosen color")
        or stream.accept_phrase("of the chosen colour")
        or stream.accept_phrase("of that color")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.ADD_MANA,
        players=YOU,
        amount=Value.of(1),
        colors_chosen=True,
        text="add mana of the chosen colour",
    )


def _replace_amount(effect: Effect, repeat: int) -> Effect:
    from dataclasses import replace as _replace

    return effect if repeat == 1 else _replace(effect, amount=Value.of(repeat))


def _mana_colour_source(stream: Stream):
    """Which colours a mana ability may produce, or ``None`` if this is not
    one of the "you choose" forms."""
    from .nouns import colour_among_tail, could_produce_tail

    for phrase in sorted(_COLOUR_SOURCES, key=lambda p: -len(p.split())):
        if stream.accept_phrase(phrase):
            for tail in _COLOUR_TAILS:
                if stream.accept_phrase(tail):
                    break
            # "of any color *that a land an opponent controls could produce*"
            # and every other source a card can name. Which source it is
            # changes nothing the grammar can act on - the engine settles the
            # available colours from the board either way - so the clause is
            # read by shape rather than listed by card.
            could_produce_tail(stream)
            colour_among_tail(stream)
            return _any_colour()

    mark = stream.mark()
    if stream.accept_phrase("of any color") or stream.accept_phrase("of any type"):
        if could_produce_tail(stream) or colour_among_tail(stream):
            return _any_colour()
        stream.reset(mark)
    return None


def _mana_run(stream: Stream) -> list[str]:
    symbols: list[str] = []
    while stream.peek().kind is TokenKind.SYMBOL:
        symbols.append(stream.next().text)
    return symbols


def _mana_effect(symbols: list[str]) -> Effect | None:
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost, UnknownManaSymbol
    from ..rules.kernel.enums import Color

    try:
        cost = ManaCost.parse("".join(symbols))
    except UnknownManaSymbol:
        return None

    colors = 0
    for symbol in cost.symbols:
        colors |= int(symbol.colors)

    return Effect(
        EffectKind.ADD_MANA,
        players=YOU,
        amount=Value.of(cost.mana_value or len(symbols)),
        colors=Color(colors),
        mana_produced=tuple(symbols),
        text="add " + "".join(symbols),
    )


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------


def _scaled(stream: Stream, amount: Value) -> Value:
    """"... for each artifact you control" - a multiplier on what came before.

    Oracle text puts the count *after* the thing it scales: "draw a card for
    each creature you control", "deals 1 damage to each opponent for each
    Mountain". Dropping the suffix would leave the effect at its base value,
    which is a quiet, plausible wrong answer - the worst kind.
    """
    mark = stream.mark()

    # "Draw cards equal to the greatest power among creatures you control" -
    # oracle text puts the amount *after* the noun as often as before it, and
    # the clauses all read their amount before. Reading the tail here means
    # every clause that already scales gets the other word order for free.
    if stream.accept_phrase("equal to"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return amount
        return _scaled(stream, value)

    from .nouns import parse_for_each

    # One reader for "for each", shared with the trailing-modifier chain.
    # Two readers is how one of them learned about counters and the other did
    # not, and the counted clauses each call whichever they happen to reach.
    multiplier = parse_for_each(stream)
    if multiplier is not None:
        if amount.is_constant and amount.constant in (0, 1):
            return multiplier
        return Value(kind=ValueKind.PRODUCT, operands=(amount, multiplier))

    if not stream.accept_phrase("for each"):
        return amount

    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return amount
    count = Value(kind=ValueKind.COUNT, players=players)

    if amount.is_constant and amount.constant == 1:
        return count
    return Value(kind=ValueKind.PRODUCT, operands=(amount, count))


def _arrives_tapped_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "tapped", "tapped and attacking", "tapped and transformed".

    The word describes how the permanent arrives, so it belongs to whichever
    effect just put it there rather than to any one verb. Only offered to
    effects that actually move something onto the battlefield: "tapped" after
    a draw would be a different word entirely.
    """
    if effect.kind not in (EffectKind.PUT_ONTO_BATTLEFIELD, EffectKind.MOVE_ZONE):
        return None
    if effect.zone is not Zone.BATTLEFIELD:
        return None
    if "tapped" in effect.keywords:
        return None

    mark = stream.mark()
    if not stream.accept("tapped"):
        return None

    extra = ["tapped"]
    while True:
        look = stream.mark()
        if not stream.accept("and"):
            break
        if stream.accept("attacking"):
            extra.append("attacking")
        elif stream.accept("transformed"):
            extra.append("transformed")
        else:
            stream.reset(look)
            break

    if stream.mark() == mark:
        return None
    from dataclasses import replace as _replace

    return _replace(effect, keywords=tuple(effect.keywords) + tuple(extra))


def _face_down_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "face down" on an exile or a put-onto-the-battlefield.

    CR 708: the object has no characteristics anyone can see. It attaches to
    the move rather than to any one verb, so it is read with the other
    trailing modifiers.
    """
    if effect.kind not in (EffectKind.EXILE, EffectKind.PUT_ONTO_BATTLEFIELD):
        return None
    mark = stream.mark()
    if not stream.accept_phrase("face down"):
        return None
    if stream.mark() == mark:
        return None
    from dataclasses import replace as _replace

    return _replace(effect, keywords=(*effect.keywords, "face down"))


def _more_targets(stream: Stream) -> list:
    """", target creature, and target enchantment" - further separate targets.

    Not a filter matching several things: each is chosen independently and
    each has its own type, so a union with a count of four would happily take
    four artifacts. Returned as a list so the caller can emit one effect per
    target.
    """
    found: list = []
    while True:
        look = stream.mark()
        stream.skip_punct(",")
        stream.accept("and")
        if not stream.at("target"):
            stream.reset(look)
            break
        spec, targeted = parse_target(stream)
        if spec is None:
            stream.reset(look)
            break
        found.append((spec, targeted))
    return found


def _in_any_order_tail(stream: Stream, effect: Effect) -> Effect | None:
    """A trailing "in any order" / "in a random order" on a library move.

    CR 401.4: several cards put into a library at once are ordered by their
    owner unless the effect says otherwise - "in any order" is that choice,
    "in a random order" takes it away. It was consumed after *any* effect and
    changed nothing, so a random bottom was a chosen one. Only a move into a
    library has an order to speak of.
    """
    if effect.kind is not EffectKind.PUT_ON_LIBRARY:
        return None
    if "random" in effect.keywords:
        return None
    mark = stream.mark()
    order = _library_order_words(stream)
    if stream.mark() == mark:
        return None
    return replace(effect, keywords=(*effect.keywords, *order))


def _under_control(stream: Stream) -> None:
    """A trailing "under its owner's control" / "under your control".

    Consumed rather than modelled: a permanent returns to its owner unless
    something says otherwise, so the phrase is the default spelled out.
    """
    mark = stream.mark()
    if not stream.accept("under"):
        return
    if (
        stream.accept_phrase("its owner's control")
        or stream.accept_phrase("their owner's control")
        or stream.accept_phrase("your control")
        or stream.accept_phrase("its controller's control")
    ):
        return
    stream.reset(mark)


def _duration(stream: Stream) -> Duration:
    """How long a continuous effect lasts (CR 611.2).

    "This turn" and "until end of turn" are the same duration written two
    ways - the first is used when the effect is phrased as an event, the
    second when it is phrased as a state. Both end in the cleanup step.
    """
    if stream.accept_phrase("until end of turn"):
        return Duration.END_OF_TURN
    if stream.accept_phrase("this turn"):
        return Duration.END_OF_TURN
    if stream.accept_phrase("until your next turn"):
        return Duration.YOUR_NEXT_TURN
    if stream.accept_phrase("until end of combat"):
        return Duration.END_OF_COMBAT
    if stream.accept_phrase("until end of your next turn"):
        return Duration.END_OF_YOUR_NEXT_TURN
    return Duration.PERMANENT


# ---------------------------------------------------------------------------
# Entering the battlefield (CR 614.1c)
# ---------------------------------------------------------------------------


@clause("as-this-enters")
def _as_this_enters(stream: Stream) -> Effect | None:
    """"As this land enters, you may pay 2 life. If you don't, it enters tapped."

    CR 614.1c: an "as ... enters" clause is a replacement, and the whole
    sentence is one effect - the choice and its consequence together. The
    shock-land cycle is the most played version of it.
    """
    mark = stream.mark()
    if not stream.accept("as"):
        stream.reset(mark)
        return None
    subject = parse_object_filter(stream)
    if subject is None or not subject.source_only:
        stream.reset(mark)
        return None
    if not stream.accept("enters", "enter"):
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    body = parse_effects(stream)
    if body is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.SEQUENCE,
        children=tuple(body),
        text="as this enters",
    )


@clause("it-enters-tapped")
def _it_enters_tapped(stream: Stream) -> Effect | None:
    """"...it enters tapped." - the consequence half of an "as it enters"."""
    mark = stream.mark()
    if not (stream.accept("it") or stream.accept("this")):
        stream.reset(mark)
        return None
    stream.accept(*SELF_NOUNS)
    if not stream.accept("enters", "enter"):
        stream.reset(mark)
        return None
    if not stream.accept("tapped"):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.TAP,
        targets=SELF,
        duration=Duration.PERMANENT,
        text="enters tapped",
    )


@clause("enters-tapped")
def _enters_tapped(stream: Stream) -> Effect | None:
    """"This land enters tapped." - a replacement effect, not an action.

    It modifies how the permanent enters rather than tapping it afterwards,
    which is why nothing sees it enter untapped and no "becomes tapped" trigger
    fires.
    """
    subject = parse_object_filter(stream)
    if subject is None or not subject.source_only:
        return None
    if not stream.accept("enters", "enter"):
        return None
    if not stream.accept("tapped"):
        return None
    return Effect(
        EffectKind.TAP,
        targets=SELF,
        duration=Duration.PERMANENT,
        text="enters tapped",
    )


@clause("enters-with-counters")
def _enters_with_counters(stream: Stream) -> Effect | None:
    """"This creature enters with two +1/+1 counters on it." (CR 614.1c)."""
    subject = parse_object_filter(stream)
    if subject is None or not subject.source_only:
        return None
    if not stream.accept("enters", "enter"):
        return None
    if not stream.accept("with"):
        return None
    amount = parse_value(stream) or Value.of(1)
    counter = _counter_word(stream)
    if counter is None:
        return None
    stream.accept_phrase("on it")
    return Effect(
        EffectKind.ADD_COUNTERS,
        counter_type=counter,
        amount=amount,
        text="enters with counters",
    )


def _counter_word(stream: Stream) -> str | None:
    token = stream.peek()
    if token.kind is TokenKind.PT:
        stream.next()
        name = token.text
    elif token.kind is TokenKind.WORD:
        stream.next()
        name = token.lower
    else:
        return None
    if not stream.accept("counter", "counters"):
        return None
    return name


# ---------------------------------------------------------------------------
# Prohibitions (CR 101.2)
# ---------------------------------------------------------------------------


@clause("player-cant")
def _player_cant(stream: Stream) -> Effect | None:
    """"Each opponent can't cast noncreature spells with mana value ...".

    A prohibition whose subject is a player and whose object is a filtered set
    of spells. Distinct from the bare "this creature can't block" form: the
    filter is the whole card, and reading it as a blanket "can't cast" would
    turn a tax effect into a lock.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    players, _ = parse_player_filter(stream)
    if players is None:
        return None
    if not (stream.accept("can't") or stream.accept("cannot")):
        return None

    # "can't cast spells *or activate abilities of* artifacts, creatures, or
    # enchantments" - one prohibition covering two acts, and reading only the
    # first turned Grand Abolisher into half a card.
    acts: list = []
    subject = None
    while True:
        if stream.accept("cast"):
            act = Act.CAST_SPELL
        elif stream.accept("play"):
            act = Act.PLAY_LAND
        elif stream.accept_phrase("search their library") or stream.accept("search"):
            act = Act.SEARCH_LIBRARY
        elif stream.accept("draw"):
            act = Act.DRAW_CARD
        elif stream.accept("gain"):
            act = Act.GAIN_LIFE
        elif stream.accept("win"):
            act = Act.WIN_GAME
        elif stream.accept_phrase("activate abilities of") or stream.accept(
            "activate"
        ):
            act = Act.ACTIVATE_ABILITY
        else:
            break

        found = parse_object_filter(stream)
        stream.accept("card", "cards", "spell", "spells", "life")
        if subject is None:
            subject = found
        acts.append(act)

        look = stream.mark()
        stream.skip_punct(",")
        if not stream.accept("or", "and"):
            stream.reset(look)
            break

    if not acts:
        return None
    act = acts[0]
    # "Your opponents can't cast spells *this turn*." A prohibition from a
    # resolved spell nearly always carries a duration, and reading it as
    # permanent turns a one-turn Silence into a lock.
    duration = _duration(stream)
    return Effect(
        EffectKind.RESTRICTION,
        players=players,
        targets=subject,
        restrictions=tuple(
            Restriction(
                act=each,
                subject=subject,
                players=players,
                text=f"can't {each.name.lower()}",
            )
            for each in acts
        ),
        duration=duration,
        text="a player can't ...",
    )


@clause("cant")
def _cant(stream: Stream) -> Effect | None:
    """"This creature can't block.", "Enchanted creature can't attack."

    A "can't" beats every "can" (CR 101.2), so these have to reach the
    restriction system rather than being modelled as an absence of something.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    subject, _ = parse_target(stream)
    if subject is None:
        return None
    if not stream.accept_phrase("can't") and not stream.accept("cannot"):
        return None

    acts: list[Act] = []
    while True:
        if stream.accept("block"):
            acts.append(Act.BLOCK)
        elif stream.accept("attack"):
            acts.append(Act.ATTACK)
            # "can't attack *you*" / "can't attack you or planeswalkers you
            # control". Who the prohibition protects is the whole point of
            # Propaganda and Ghostly Prison; without it the restriction reads
            # as "can't attack at all", which is a far stronger card.
            look = stream.mark()
            protected, _ = parse_player_filter(stream)
            if protected is None:
                stream.reset(look)
            else:
                look = stream.mark()
                if stream.accept("or"):
                    if parse_object_filter(stream) is None:
                        stream.reset(look)
        elif stream.accept_phrase("be blocked"):
            acts.append(Act.BE_BLOCKED)
        elif stream.accept_phrase("be countered"):
            acts.append(Act.BE_COUNTERED)
        elif stream.accept_phrase("be regenerated"):
            acts.append(Act.BE_REGENERATED)
        elif stream.accept_phrase("be targeted"):
            acts.append(Act.BE_TARGETED)
        elif stream.accept("untap"):
            acts.append(Act.UNTAP)
        else:
            break
        # "can't attack or block", "can't attack and can't block" - the same
        # prohibition written two ways. Requiring the second "can't" meant the
        # shorter and commoner form stopped after the first act.
        #
        # The separator is only consumed when another *act* follows it.
        # "can't be blocked and has shroud" ends the list at "blocked", and
        # swallowing that "and" left the grant half with no conjunction to
        # recognise itself by.
        look = stream.mark()
        stream.skip_punct(",")
        if not stream.accept("or", "and"):
            stream.reset(look)
            break
        stream.accept_phrase("can't")
        if not _starts_an_act(stream):
            stream.reset(look)
            break

    if not acts:
        return None

    # "Equipped creature can't be blocked *and has shroud*." One sentence,
    # one subject, two different layers - exactly like "gets +2/+2 and has
    # trample", which was read, while this was not.
    granted = _also_gains(stream)

    # "Target creature can't be blocked *this turn*". A prohibition from a
    # resolved spell nearly always has a duration, and reading it as permanent
    # left the phrase unconsumed and failed the whole ability - which is why
    # "this turn ." was the single largest remaining failure cluster.
    duration = _duration(stream)
    forbidden = Effect(
        EffectKind.RESTRICTION,
        targets=subject,
        restrictions=tuple(
            Restriction(act=act, subject=subject, text=f"can't {act.name.lower()}")
            for act in acts
        ),
        duration=duration,
        text="can't",
    )
    if not granted:
        return forbidden
    return Effect(
        EffectKind.SEQUENCE,
        children=(
            forbidden,
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=subject,
                granted_abilities=granted,
                duration=duration,
                text="and has " + ", ".join(a.keyword or a.text for a in granted),
            ),
        ),
        text="can't, and has abilities",
    )


#: The words that begin a prohibited act, for deciding whether a conjunction
#: continues the list or starts a new half of the sentence.
_ACT_WORDS = ("block", "attack", "untap", "be")


def _starts_an_act(stream: Stream) -> bool:
    """Whether the next token begins another prohibited act."""
    return stream.peek().lower in _ACT_WORDS


@clause("doesnt-untap")
def _doesnt_untap(stream: Stream) -> Effect | None:
    """"Enchanted creature doesn't untap during its controller's untap step."

    The permanent form only. "Doesn't untap during its controller's *next*
    untap step" is a different card - one turn of freeze rather than a lock -
    and there is no ``Duration`` that ends at a particular player's next untap
    step, so it cannot be told apart from the permanent form once built.

    It used to be told apart by nobody: the "next" was left on the stream and
    swallowed by a rule that ate any trailing "during ...", so every Frost
    Breath and every Sleep in the format tapped a board down *permanently*.
    Declining is the version of this the coverage report can see.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    subject, _ = parse_target(stream)
    if subject is None:
        return None
    if not (stream.accept("doesn't") or stream.accept_phrase("does not")):
        return None
    if not stream.accept("untap"):
        return None
    if not (
        stream.accept_phrase("during its controller's untap step")
        or stream.accept_phrase("during your untap step")
        or stream.accept_phrase("during their untap step")
    ) and stream.at("during"):
        return None
    return Effect(
        EffectKind.RESTRICTION,
        targets=subject,
        restrictions=(
            Restriction(
                act=Act.UNTAP_DURING_UNTAP_STEP,
                subject=subject,
                text="doesn't untap during its controller's untap step",
            ),
        ),
        duration=Duration.PERMANENT,
        text="doesn't untap",
    )


# ---------------------------------------------------------------------------
# Optional and conditional
# ---------------------------------------------------------------------------


@clause("as-though-flash")
def _as_though_flash(stream: Stream) -> Effect | None:
    """"You may cast spells as though they had flash." (CR 113.6, CR 307.1).

    A *permission*, not a prohibition with a "not" in front - which is why it
    needs its own layer in the engine. The rules are maximally restrictive by
    default, so without something that says "yes" these cards do nothing at
    all.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("may"):
        stream.reset(mark)
        return None
    if not stream.accept("cast", "play"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None

    # "You may cast spells *this turn* as though they had flash" puts the
    # duration in the middle of the sentence; the more usual wording puts it
    # at the end. Reading it in both places costs one line and covers both.
    duration = _duration(stream)

    if not (
        stream.accept_phrase("as though they had flash")
        or stream.accept_phrase("as though it had flash")
        or stream.accept_phrase("as though they had flash this turn")
    ):
        stream.reset(mark)
        return None

    # "Spells" as a noun phrase implies the stack, which is where a spell
    # lives - but a permission about *casting* is asked while the card is
    # still in hand. Keeping the zone here made the permission match nothing
    # and the card do nothing.
    castable = replace(spec, zones=frozenset())

    if duration is Duration.PERMANENT:
        duration = _duration(stream)
    return Effect(
        EffectKind.PERMISSION,
        targets=castable,
        players=players,
        duration=duration,
        restrictions=(
            Restriction(
                act=Act.CAST_AS_THOUGH_FLASH,
                subject=castable,
                players=players,
                text="cast as though it had flash",
            ),
        ),
        text="may cast as though it had flash",
    )


@clause("you-may")
def _you_may(stream: Stream) -> Effect | None:
    """"You may draw a card", "Its controller may search their library".

    The chooser is whoever the sentence names. Hardcoding "you" meant that
    every card handing an *opponent* a choice - Path to Exile among them -
    had no clause at all, and the option silently belonged to the wrong
    player wherever it did parse.
    """
    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("may"):
        stream.reset(mark)
        return None
    body = stream.mark()
    inner = parse_effect(stream)
    if inner is None:
        stream.reset(mark)
        return None
    if players.scope not in (
        PlayerScope.YOU,
        # Still decided by the controller in the engine; see
        # ``resolve._CONTROLLER_CHOOSES``.
        PlayerScope.EACH_PLAYER,
        PlayerScope.EACH_OPPONENT,
        PlayerScope.OPPONENT,
    ) and any(
        token.lower in ("you", "your", "yours", "you've", "you're")
        for token in stream.tokens[body : stream.mark()]
    ):
        # The engine carries out another player's "may" with that player as
        # its "you" (CR 608.2d) - which is what the sentence's own subject-
        # less verbs and "their" mean. A "you" inside it is the ability's
        # controller, a second player the IR cannot tell apart from the
        # first, so the sentence is refused rather than handed to the wrong
        # one.
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.OPTIONAL, children=(inner,), players=players, text="may"
    )


@clause("if-it-happened")
def _if_it_happened(stream: Stream) -> Effect | None:
    """"If a card is exiled this way, ...", "If a creature dies this way, ...".

    CR 603.9's reflexive shape written about the *object* the preceding
    effect acted on rather than about the player's choice. "This way" is the
    giveaway: it refers back to what just happened, which the resolution
    already remembers, so the condition is whether anything was acted on.

    Massimo, the Magician is the motivating card, and it is worth being exact
    about what it does: the second half *grants the creature an ability*.
    That grant comes from a resolving ability with no duration, so by
    CR 611.2b the creature keeps it after Massimo leaves - and only loses it
    by changing zones (CR 400.7).
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    if not stream.accept("if"):
        stream.reset(mark)
        return None

    # The subject of the condition, up to "this way".
    steps = 0
    while not stream.done and not stream.at_phrase("this way"):
        if stream.peek().text in (".", ";", ","):
            break
        stream.next()
        steps += 1
        if steps > 12:
            stream.reset(mark)
            return None
    if not stream.accept_phrase("this way"):
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    body = parse_effects(stream)
    if body is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.REFLEXIVE_TRIGGER,
        children=tuple(body),
        condition=Condition(
            kind=ConditionKind.NEVER, text="if it happened this way"
        ),
        text="if ... this way",
    )


@clause("if-you-dont")
def _if_you_dont(stream: Stream) -> Effect | None:
    """"If you don't, <effect>." - the other half of an optional action.

    CR 603.9's reflexive trigger has a negative twin: "You may sacrifice a
    creature. If you don't, draw a card." Only the positive form was read, so
    every card that punishes declining an option failed - and these are
    common, because "may ... if you don't" is how a drawback is written.
    """
    mark = stream.mark()
    if not stream.accept("if"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("you don't")
        or stream.accept_phrase("you do not")
        or stream.accept_phrase("they don't")
        or stream.accept_phrase("that player doesn't")
        or stream.accept_phrase("the player doesn't")
        or stream.accept_phrase("that player does not")
        or stream.accept_phrase("they do not")
    ):
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    body = parse_effects(stream)
    if body is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.IF_YOU_DONT,
        children=tuple(body),
        text="if you don't",
    )


@clause("no-maximum-hand-size")
def _no_maximum_hand_size(stream: Stream) -> Effect | None:
    """"You have no maximum hand size." (CR 402.2)."""
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept_phrase("have no maximum hand size") and not (
        stream.accept_phrase("has no maximum hand size")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.PERMISSION,
        players=players,
        restrictions=(
            Restriction(
                act=Act.DISCARD_TO_HAND_SIZE,
                players=players,
                text="skip the cleanup discard",
            ),
        ),
        text="no maximum hand size",
    )


@clause("additional-land")
def _additional_land(stream: Stream) -> Effect | None:
    """"You may play an additional land on each of your turns." (CR 305.2)."""
    mark = stream.mark()
    # The subject is optional: "You may play an additional land" reaches here
    # with "You may" already eaten by the general optional clause, so the
    # clause has to read from "play" as well as from the start.
    players, _ = parse_player_filter(stream)
    stream.accept("may")
    if not stream.accept("play"):
        stream.reset(mark)
        return None
    players = players or YOU

    amount = parse_value(stream) or Value.of(1)
    if not stream.accept("additional"):
        stream.reset(mark)
        return None
    if not stream.accept("land", "lands"):
        stream.reset(mark)
        return None
    # When the extra drop applies. "This turn" is the one-shot version -
    # Explore and its family - and the rest are the default anyway.
    stream.accept_phrase("on each of your turns")
    stream.accept_phrase("each turn")
    stream.accept_phrase("this turn")
    return Effect(
        EffectKind.EXTRA_LAND_DROP,
        players=players,
        amount=amount,
        text="an additional land drop",
    )


@clause("if-you-do")
def _if_you_do(stream: Stream) -> Effect | None:
    """"If you do, draw a card." - CR 603.9's reflexive trigger.

    Not a delayed trigger and not a plain sequence: it happens *because* the
    preceding optional action was taken, and not at all if it was not.
    """
    mark = stream.mark()
    if not (stream.accept_phrase("if you do") or stream.accept_phrase("when you do")):
        return None
    stream.skip_punct(",")
    inner = parse_effects(stream)
    if inner is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.REFLEXIVE_TRIGGER, children=tuple(inner), text="if you do"
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


#: The little steps that follow a search, in the order the card happens to
#: write them. Each maps to the zone the found card ends up in, or ``None``
#: for a step that is bookkeeping the engine already does.
#:
#: A loop rather than a fixed pair, because tutors combine these freely -
#: "reveal it, put it into your hand, then shuffle", "put that card onto the
#: battlefield tapped, then shuffle", "reveal that card and put it into your
#: hand" - and a rigid tail failed every ordering it had not been told about.
#: Path to Exile, Cultivate and most of the format's ramp died here.
_SEARCH_STEPS: tuple[tuple[str, "Zone | None"], ...] = (
    ("put that card onto the battlefield", Zone.BATTLEFIELD),
    ("put it onto the battlefield", Zone.BATTLEFIELD),
    ("put them onto the battlefield", Zone.BATTLEFIELD),
    ("put those cards onto the battlefield", Zone.BATTLEFIELD),
    ("put that card into your hand", Zone.HAND),
    ("put it into your hand", Zone.HAND),
    ("put them into your hand", Zone.HAND),
    ("put those cards into your hand", Zone.HAND),
    ("put that card into your graveyard", Zone.GRAVEYARD),
    ("put it into your graveyard", Zone.GRAVEYARD),
    ("put them into your graveyard", Zone.GRAVEYARD),
    # The tutors that leave the card on top (Vampiric Tutor, Worldly Tutor
    # and their cycles) always shuffle *first* and then put it on top; the
    # tail checks that order, because the other order would shuffle the card
    # away.
    ("put that card on top of your library", Zone.LIBRARY),
    ("put it on top of your library", Zone.LIBRARY),
    ("put that card on top", Zone.LIBRARY),
    ("put the card on top", Zone.LIBRARY),
    ("put it on top", Zone.LIBRARY),
    ("exile that card", Zone.EXILE),
    ("exile it", Zone.EXILE),
    ("exile them", Zone.EXILE),
    ("exile those cards", Zone.EXILE),
    # CR 701.23e: revealing is part of the instruction but changes nothing
    # about where the card goes; the engine emits no reveal for a search.
    ("reveal that card", None),
    ("reveal those cards", None),
    ("reveal it", None),
    ("reveal them", None),
    # The shuffle every search ends with (CR 701.23) - the executor does it.
    ("shuffle your library", None),
    ("shuffle their library", None),
    ("shuffle", None),
)

#: Cultivate and Kodama's Reach: of the (up to) two cards found, one goes onto
#: the battlefield tapped and the other into the hand. Read as its own step,
#: because it names two destinations, not one.
_SEARCH_SPLIT = "put one onto the battlefield tapped and the other into your hand"


def _search_tail(stream: Stream) -> tuple[Zone, bool, bool] | None:
    """Where a search puts what it finds, whether it arrives tapped, and
    whether the found cards are split between the battlefield and the hand.

    Defaults to the hand (CR 701.23c: a search with no stated destination
    reveals nothing and the card stays where the effect says - hand is the
    overwhelmingly common templating). ``None`` when the steps are in an order
    the executor would not follow: a card put on top and *then* shuffled is
    not on top.

    Only steps whose meaning is carried are read. "Discard a card at random",
    "pay 2 life", "put the rest into your graveyard" were once consumed here
    and dropped, which turned Gamble into a free Demonic Tutor; the tail now
    stops at them and they must be read as effects of their own.
    """
    destination = Zone.HAND
    tapped = False
    split = False
    shuffled = False
    while True:
        before = stream.mark()
        stream.skip_punct(",")
        stream.accept("then", "and")
        stream.skip_punct(",")

        if stream.accept_phrase(_SEARCH_SPLIT):
            destination, tapped, split = Zone.BATTLEFIELD, True, True
            continue
        for phrase, zone in _SEARCH_STEPS:
            if not stream.accept_phrase(phrase):
                continue
            if zone is not None:
                if zone is Zone.LIBRARY and not shuffled:
                    return None
                destination = zone
            if phrase.startswith("shuffle"):
                if destination is Zone.LIBRARY:
                    return None
                shuffled = True
            # "put it onto the battlefield tapped" - how it arrives (CR
            # 614.1c), a word after the step rather than a step of its own.
            if zone is Zone.BATTLEFIELD and stream.accept("tapped"):
                tapped = True
            break
        else:
            stream.reset(before)
            return destination, tapped, split


@clause("search")
def _search(stream: Stream) -> Effect | None:
    """"Search your library for a basic land card, put it onto the battlefield"."""
    players, _ = parse_player_filter(stream)
    if not stream.accept("search", "searches"):
        return None
    stream.accept("your", "their", "his", "her")
    if not stream.accept("library", "libraries"):
        return None
    if not stream.accept("for"):
        return None
    spec = parse_object_filter(stream)
    if spec is None:
        return None

    tail = _search_tail(stream)
    if tail is None:
        return None
    destination, tapped, split = tail
    if split and not (spec.up_to and spec.count == Value.of(2)):
        # "one ... and the other" names exactly two cards.
        return None

    keywords: tuple[str, ...] = ()
    if tapped and destination is Zone.BATTLEFIELD:
        # Carried the same way every other put-onto-the-battlefield carries
        # it, so the executor has one thing to look for (CR 614.1c).
        keywords += ("tapped",)
    if split:
        keywords += ("other to hand",)
    return Effect(
        EffectKind.SEARCH_LIBRARY,
        players=players or YOU,
        targets=spec,
        zone=destination,
        # "Up to two basic land cards" finds up to two: the count is the
        # filter's, and "a card" is one. "Any number of" has no count; the
        # executor reads that off the filter (``chosen_count``).
        amount=spec.count if spec.count is not None else Value.of(1),
        keywords=keywords,
        text="search library",
    )


# ---------------------------------------------------------------------------
# MTG Arena's conjure and seek (see rules/cr700_additional_rules/digital_mechanics)
# ---------------------------------------------------------------------------


@clause("conjure")
def _conjure(stream: Stream) -> Effect | None:
    """"Conjure a card named Lightning Bolt into your hand", "conjure four
    cards named Volcanic Geyser into your library, then shuffle", "conjure a
    duplicate of it onto the battlefield", "conjure a card named this ...".

    The name is everything between "named" and the destination, as printed -
    the engine looks it up when the effect happens. Only the destinations the
    engine can place a card in are read; "into the top five cards of your
    library at random", "onto the battlefield attached to ...", and a library
    with no position and no shuffle after it are left unread.
    """
    mark = stream.mark()
    stream.accept("you")
    if not stream.accept("conjure"):
        stream.reset(mark)
        return None

    amount = _conjure_count(stream)
    if amount is None:
        stream.reset(mark)
        return None

    card_name = ""
    targets = None
    keywords: list[str] = []
    if stream.accept("duplicate", "duplicates"):
        if not stream.accept("of"):
            stream.reset(mark)
            return None
        start = stream.mark()
        targets, targeted = parse_target(stream)
        if targets is None or targets.includes_players or targets.count is not None:
            stream.reset(mark)
            return None
        if not _conjure_source_ok(stream, start, targets):
            stream.reset(mark)
            return None
        keywords.append("duplicate")
    elif stream.accept("card", "cards"):
        if not stream.accept("named"):
            stream.reset(mark)
            return None
        if stream.accept("this"):
            # Normalisation wrote the card's own name as "this": a card with
            # this card's name, which is this card's own definition.
            targets, targeted = SELF, False
        else:
            card_name = _conjured_name(stream)
            if not card_name:
                stream.reset(mark)
                return None
            targeted = False
    else:
        stream.reset(mark)
        return None

    destination = _conjure_destination(stream)
    if destination is None:
        stream.reset(mark)
        return None
    zone, tapped, from_top = destination
    if tapped:
        keywords.append("tapped")
    if card_name:
        from ..rules.cr700_additional_rules.digital_mechanics import named

        keywords.append(named(card_name))
    return Effect(
        EffectKind.CONJURE,
        players=YOU,
        targets=targets,
        is_targeted=targeted,
        zone=zone,
        amount=amount,
        amount2=Value.of(from_top),
        keywords=tuple(keywords),
        text="conjure",
    )


def _conjure_count(stream: Stream) -> Value | None:
    number = stream.accept_number()
    if number is not None:
        return Value.of(number)
    if stream.accept("X"):
        return Value(kind=ValueKind.X)
    return None


def _conjure_source_ok(stream: Stream, start: int, spec: ObjectFilter) -> bool:
    """A duplicate of a card elsewhere must name whose zone it is in."""
    from ..rules.kernel.query import ControllerRelation

    words = [token.lower for token in stream.tokens[start : stream.mark()]]
    if "random" in words:
        return False
    offboard = spec.zones and Zone.BATTLEFIELD not in spec.zones
    if offboard and "in" in words:
        # "a duplicate of target card in an opponent's graveyard" is read with
        # the opponent; "in your graveyard" loses its "your" in the noun
        # reader, so only the opponent form is trusted.
        return spec.controller is ControllerRelation.OPPONENT or spec.owner is not ControllerRelation.ANY
    return True


#: What may follow a conjured card's name.
_CONJURE_DESTINATIONS = (
    ("onto the battlefield tapped", Zone.BATTLEFIELD, True),
    ("onto the battlefield", Zone.BATTLEFIELD, False),
    ("into your hand", Zone.HAND, False),
    ("into your graveyard", Zone.GRAVEYARD, False),
    ("into your library", Zone.LIBRARY, False),
    ("into exile", Zone.EXILE, False),
    ("on top of your library", Zone.LIBRARY, False),
)


def _at_conjure_destination(stream: Stream) -> bool:
    return any(stream.at_phrase(phrase) for phrase, _, _ in _CONJURE_DESTINATIONS)


def _conjured_name(stream: Stream) -> str:
    """The printed name after "named", up to the destination.

    A name starts with a capital and runs to the first word that begins a
    destination; commas and apostrophes are part of names ("Fblthp, the
    Lost"). Nothing is guessed: no destination, no name.
    """
    words: list[str] = []
    first = stream.peek()
    if first.kind is not TokenKind.WORD or not first.text[:1].isupper():
        return ""
    while not stream.done:
        if words and _at_conjure_destination(stream):
            break
        token = stream.peek()
        if token.text in (".", ";", '"'):
            return ""
        words.append(token.text)
        stream.next()
        if len(words) > 10:
            return ""
    if stream.done:
        return ""
    name = ""
    for word in words:
        if word == ",":
            name += ","
        elif not name:
            name = word
        else:
            name += " " + word
    return name


def _conjure_destination(stream: Stream):
    """(zone, tapped, position from the top) - or ``None``."""
    for phrase, zone, tapped in _CONJURE_DESTINATIONS:
        if not stream.accept_phrase(phrase):
            continue
        if zone is Zone.BATTLEFIELD and stream.at("attached", "and", "under"):
            if stream.at_phrase("and attacking") or stream.at("attached", "under"):
                return None
        if phrase == "on top of your library":
            return zone, False, 1
        if zone is Zone.LIBRARY:
            ordinal = stream.peek().lower
            from .tokens import ORDINALS

            if ordinal in ORDINALS and stream.peek(1).lower == "from":
                stream.next()
                if not stream.accept_phrase("from the top"):
                    return None
                return zone, False, ORDINALS[ordinal]
            # No position: only a library about to be shuffled is fine.
            look = stream.mark()
            stream.skip_punct(",", ".")
            stream.accept("then")
            shuffled = stream.accept("shuffle")
            stream.reset(look)
            if not shuffled:
                return None
            return zone, False, 0
        return zone, tapped, 0
    return None


@clause("seek")
def _seek(stream: Stream) -> Effect | None:
    """"Seek a land card", "seek two nonland cards", "seek a land card and a
    nonland card", "seek that many nonland cards".

    Only from your own library, which is the only kind printed without a
    subject; "seek ... from among the top ten cards" is a different pool and
    is left unread.
    """
    mark = stream.mark()
    stream.accept("you")
    if not stream.accept("seek"):
        stream.reset(mark)
        return None

    seeks = []
    while True:
        one = _one_seek(stream)
        if one is None:
            stream.reset(mark)
            return None
        seeks.append(one)
        look = stream.mark()
        if stream.accept("and") and stream.peek().kind is TokenKind.NUMBER:
            continue
        stream.reset(look)
        break
    if len(seeks) == 1:
        return seeks[0]
    return Effect(EffectKind.SEQUENCE, children=tuple(seeks), text="seek")


def _one_seek(stream: Stream) -> Effect | None:
    start = stream.mark()
    # "seeks that many nonland cards" counts whatever the sentence before did
    # ("discards all the cards in their hand"), which no value here can name.
    amount = _conjure_count(stream)
    if amount is None:
        stream.reset(start)
        return None
    noun_start = stream.mark()
    # "a land card *and a nonland card*" is two seeks; the noun reader would
    # read both as one filter (a card that is land and nonland), so it is
    # given only the words up to the second count.
    end = noun_start
    while end < len(stream.tokens) and stream.tokens[end].text not in (".", ";"):
        if (
            stream.tokens[end].lower == "and"
            and end + 1 < len(stream.tokens)
            and stream.tokens[end + 1].kind is TokenKind.NUMBER
        ):
            break
        end += 1
    sub = Stream(stream.tokens[:end], pos=noun_start)
    spec = parse_object_filter(sub)
    if spec is None:
        stream.reset(start)
        return None
    stream.reset(sub.mark())
    words = [t.lower for t in stream.tokens[noun_start : stream.mark()]]
    if "card" not in words and "cards" not in words:
        stream.reset(start)
        return None
    if (
        spec.count is not None
        or spec.remembered
        or spec.source_only
        or spec.zones not in (frozenset(), ObjectFilter().zones)
        or spec.controller is not ObjectFilter().controller
        or spec.owner is not ObjectFilter().owner
        or any(w in ("permanent", "permanents") for w in words)
    ):
        # A zone, owner or controller of its own is a different pool; and
        # "permanent card" is read as a card of any type by the noun reader.
        stream.reset(start)
        return None
    return Effect(
        EffectKind.SEEK,
        players=YOU,
        targets=replace(spec, zones=frozenset({Zone.LIBRARY})),
        amount=amount,
        zone=Zone.HAND,
        text="seek",
    )


@clause("return-from-graveyard")
def _return_from_graveyard(stream: Stream) -> Effect | None:
    """"Return target creature card from your graveyard to the battlefield."."""
    if not stream.accept("return", "returns"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("from"):
        return None
    origin = parse_zone(stream)
    if origin is None:
        return None
    if not stream.accept("to"):
        return None
    destination = parse_zone(stream)
    if destination is None:
        if stream.accept_phrase("its owner's hand") or stream.accept_phrase(
            "your hand"
        ):
            destination = Zone.HAND
        else:
            return None
    kind = (
        EffectKind.PUT_ONTO_BATTLEFIELD
        if destination is Zone.BATTLEFIELD
        else EffectKind.MOVE_ZONE
    )
    # "Return all land cards from your graveyard to the battlefield *tapped*."
    # The put-onto-the-battlefield clause already read this tail and this one
    # did not, so mass reanimation arrived untapped and ready to attack.
    tapped = stream.accept("tapped")
    _under_control(stream)
    if targets.source_only:
        # "Return this card from your graveyard to your hand" looks for the
        # card where the sentence says it is. Left on the battlefield, the
        # filter never found it and Eternal Dragon's ability did nothing; and
        # a card exiled in response must not come back from exile.
        targets = replace(targets, zones=frozenset({origin}))
    return Effect(
        kind,
        targets=targets,
        from_zone=origin,
        zone=destination,
        is_targeted=targeted,
        keywords=("tapped",) if tapped else (),
        text="return from a zone",
    )


@clause("put-onto-battlefield")
def _put_onto_battlefield(stream: Stream) -> Effect | None:
    """"Put target creature card from your graveyard onto the battlefield."."""
    mark = stream.mark()
    # "*Each player* puts all cards they exiled this way onto the
    # battlefield" - the putter need not be you, and reading it as you turns
    # a symmetrical effect into a one-sided one.
    parse_player_filter(stream)
    if not stream.accept("put", "puts"):
        stream.reset(mark)
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    origin = None
    if stream.accept("from"):
        origin = parse_zone(stream)
        if origin is None:
            return None
    if not stream.accept_phrase("onto the battlefield"):
        return None
    tapped = stream.accept("tapped")
    _under_control(stream)
    return Effect(
        EffectKind.PUT_ONTO_BATTLEFIELD,
        targets=targets,
        from_zone=origin,
        zone=Zone.BATTLEFIELD,
        is_targeted=targeted,
        keywords=("tapped",) if tapped else (),
        text="put onto the battlefield",
    )


@clause("exile-until")
def _exile_until(stream: Stream) -> Effect | None:
    """"Exile target creature until this creature leaves the battlefield."

    The "until" half is what makes it a temporary answer rather than removal,
    and dropping it would turn every flicker-removal creature into a Swords to
    Plowshares.
    """
    if not stream.accept("exile", "exiles"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("until"):
        return None
    if not (
        stream.accept_phrase("this creature leaves the battlefield")
        or stream.accept_phrase("this permanent leaves the battlefield")
        or stream.accept_phrase("this leaves the battlefield")
    ):
        return None
    return Effect(
        EffectKind.EXILE,
        targets=targets,
        is_targeted=targeted,
        duration=Duration.WHILE_SOURCE_PERSISTS,
        text="exile until this leaves",
    )


# ---------------------------------------------------------------------------
# Static shapes: "as long as", "costs less", base P/T, requirements
# ---------------------------------------------------------------------------


@clause("as-long-as")
def _as_long_as(stream: Stream) -> Effect | None:
    """"As long as <condition>, <effect>." - a conditioned continuous effect.

    The condition is checked continuously rather than once, which is what
    separates it from a one-shot. It is attached to the effect rather than to
    the ability so that a sentence with two clauses keeps them independent.
    """
    if not stream.accept_phrase("as long as"):
        return None
    condition = _condition(stream)
    if condition is None:
        return None
    stream.skip_punct(",")
    inner = parse_effects(stream)
    if inner is None:
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=condition,
        children=tuple(inner),
        duration=Duration.WHILE_SOURCE_PERSISTS,
        text="as long as",
    )


@clause("if-condition")
def _if_condition(stream: Stream) -> Effect | None:
    """"If <condition>, <effect>." - checked once, when this resolves."""
    if not stream.accept("if"):
        return None
    condition = _condition(stream)
    if condition is None:
        return None
    stream.skip_punct(",")
    inner = parse_effects(stream)
    if inner is None:
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=condition,
        children=tuple(inner),
        text="if",
    )


def parse_condition_text(stream: Stream):
    """Public entry to the condition grammar, for callers outside this module."""
    return _condition(stream)


class PlayerScopeName:
    """The name of a PlayerScope, resolved late.

    The table above is module-level and the query module is imported inside
    the functions that need it, to keep the parser importable without pulling
    the whole engine in behind it.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _condition(stream: Stream):
    """One condition, and any others joined to it by "and" or "or".

    Conjunction is an operator, not a shape: any clause can be joined to any
    other. AND, OR and NOT have been condition kinds since the engine was
    written and nothing in the grammar produced one, so every compound clause
    - "as long as this card is in your graveyard and you control a Mountain"
    - failed on the conjunction.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    first = _single_condition(stream)
    if first is None:
        return None

    operands = [first]
    kind = None
    while True:
        mark = stream.mark()
        stream.skip_punct(",")
        if stream.accept("and"):
            joined = ConditionKind.AND
        elif stream.accept("or"):
            joined = ConditionKind.OR
        else:
            stream.reset(mark)
            break
        # A mixed "A and B or C" is not something oracle text writes; the
        # first conjunction fixes which operator this clause is.
        if kind is not None and joined is not kind:
            stream.reset(mark)
            break
        nxt = _single_condition(stream)
        if nxt is None:
            stream.reset(mark)
            break
        kind = joined
        operands.append(nxt)

    if kind is None:
        return first
    return Condition(
        kind=kind,
        operands=tuple(operands),
        text=" and ".join(str(each) for each in operands),
    )


def _single_condition(stream: Stream):
    """The conditions that appear often enough to be worth reading.

    An unreadable condition returns ``None`` and fails the ability, rather
    than defaulting to true - a condition read as always-true makes the card
    do something it should only sometimes do, which is strictly worse than
    doing nothing.

    Every branch reads its quantity itself and says exactly how it is
    compared. An earlier "you have ..." branch accepted a number and then
    skipped any of "or more", "or fewer", "life", "cards", "in hand" and
    "this turn" as it found them, and answered with a life total: "as long as
    you have one or fewer cards in hand" became "you have 1 or more life",
    true for the whole game.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()

    from .triggers import designation_condition

    designation = designation_condition(stream)
    if designation is not None:
        return designation

    about_it = _about_the_remembered(stream)
    if about_it is not None:
        return about_it

    this_turn = _happened_this_turn(stream)
    if this_turn is not None:
        return this_turn

    opponents = _opponent_count(stream)
    if opponents is not None:
        return opponents

    there_are = _there_are(stream)
    if there_are is not None:
        return there_are

    comparison = _controls_more_than(stream)
    if comparison is not None:
        return comparison

    controls = _player_controls(stream)
    if controls is not None:
        return controls

    # "<player> has N or more life", "<player> has no cards in hand".
    life = _life_condition(stream)
    if life is not None:
        return life
    hand = _hand_condition(stream)
    if hand is not None:
        return hand

    # "this creature has four or more +1/+1 counters on it".
    counters = _counter_condition(stream)
    if counters is not None:
        return counters

    if stream.accept_phrase("you cast it") or stream.accept_phrase(
        "you cast this spell"
    ):
        return Condition(kind=ConditionKind.WAS_CAST, text="you cast it")

    supertype = _is_supertype(stream)
    if supertype is not None:
        return supertype

    if stream.accept_phrase("it's your turn"):
        return Condition(kind=ConditionKind.IS_YOUR_TURN, text="it's your turn")

    if stream.accept_phrase("it's not your turn"):
        # The Force cycle's window. Written as its own branch rather than by
        # a general negation reader, because "not" attaches to different
        # words in different clauses and a blanket rule would misplace it.
        return Condition(
            kind=ConditionKind.NOT,
            operands=(
                Condition(kind=ConditionKind.IS_YOUR_TURN, text="it's your turn"),
            ),
            text="it's not your turn",
        )

    # "as long as this artifact is untapped", "as long as this creature is
    # attacking". A state on the source, which the object filter can already
    # express - so it is read as a filter and asked as a count of one.
    state = _state_condition(stream)
    if state is not None:
        return state

    value = _value_condition(stream)
    if value is not None:
        return value

    stream.reset(mark)
    return None


def _condition_quantity(stream: Stream):
    """How many, in a condition: "three or more", "two or fewer", "no",
    "exactly two", "at least five", "a", "one or more".

    Read here rather than taken from the noun phrase, because the noun reader
    folds "three or more" and a bare "three" into the same count - which is
    right for choosing targets and wrong for a test. A bare number above one
    is not a condition oracle text writes ("if you control three artifacts"
    would be "three or more"), so it is declined rather than guessed at.
    """
    from ..rules.kernel.query import NumericConstraint

    mark = stream.mark()
    if stream.accept("no"):
        return NumericConstraint.exactly(0)
    if stream.at("another"):
        # "another Goblin" - one, other than the source. The word itself is
        # left for the noun reader, which records the exclusion.
        return NumericConstraint.at_least(1)
    if stream.accept("exactly"):
        amount = stream.accept_number()
        if amount is None:
            stream.reset(mark)
            return None
        return NumericConstraint.exactly(amount)
    if stream.accept_phrase("at least"):
        amount = stream.accept_number()
        if amount is None:
            stream.reset(mark)
            return None
        return NumericConstraint.at_least(amount)
    amount = stream.accept_number()
    if amount is None:
        return None
    if stream.accept_phrase("or more") or stream.accept_phrase("or greater"):
        return NumericConstraint.at_least(amount)
    if stream.accept_phrase("or fewer") or stream.accept_phrase("or less"):
        return NumericConstraint.at_most(amount)
    if amount == 1:
        # "a Forest", "an artifact", "one creature" - at least one.
        return NumericConstraint.at_least(1)
    stream.reset(mark)
    return None


def _counted_filter(stream: Stream):
    """A quantity and the noun phrase it counts, or ``None`` for both.

    The noun phrase must not carry a count of its own: the quantity was
    already read, and a second one ("two or more three creatures") is not
    something a condition says.
    """
    mark = stream.mark()
    another = stream.at("another")
    constraint = _condition_quantity(stream)
    if constraint is None:
        return None, None
    spec = parse_object_filter(stream)
    if (
        another
        and spec is not None
        and spec.other_than_source
        and spec.count == Value.of(1)
        and not spec.up_to
    ):
        # "another Goblin" carries its "one" inside the word, and the noun
        # reader counts it; the condition has already read that "one" as
        # its constraint, so it is not a second count.
        spec = replace(spec, count=None)
    if spec is None or spec.count is not None:
        stream.reset(mark)
        return None, None
    return constraint, spec


def _there_are(stream: Stream):
    """"there are seven or more cards in your graveyard", "there are no
    creatures on the battlefield" - a count with no controller in it."""
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    if not (stream.accept_phrase("there are") or stream.accept_phrase("there is")):
        return None
    constraint, spec = _counted_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    return Condition(
        kind=ConditionKind.OBJECT_COUNT,
        filter=spec,
        constraint=constraint,
        text="there are ...",
    )


def _player_controls(stream: Stream):
    """"you control three or more artifacts", "an opponent controls two or
    more creatures", "no opponent controls a Forest", "you don't control a
    Swamp".

    Who counts matters as much as what is counted. "An opponent controls
    three or more creatures" is about one opponent (CR 102.3), so it is asked
    opponent by opponent; it used to be read as "an opponent controls a
    creature", the count dropped outright.
    """
    from ..rules.kernel.query import (
        Condition,
        ConditionKind,
        ControllerRelation,
        NumericConstraint,
        PlayerFilter,
        PlayerScope,
    )

    mark = stream.mark()
    negated = False
    if stream.accept_phrase("you control"):
        scope = PlayerScope.YOU
    elif stream.accept_phrase("you don't control") or stream.accept_phrase(
        "you do not control"
    ):
        scope, negated = PlayerScope.YOU, True
    elif stream.accept_phrase("an opponent controls"):
        scope = PlayerScope.OPPONENT
    elif stream.accept_phrase("no opponent controls"):
        scope, negated = PlayerScope.OPPONENT, True
    elif stream.accept_phrase("each opponent controls"):
        scope = PlayerScope.EACH_OPPONENT
    else:
        return None

    constraint, spec = _counted_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if negated:
        # "you don't control a Swamp": the quantity is the indefinite article
        # and nothing else. "You don't control three or more" is not a shape
        # oracle text uses, and inverting it by hand is how a bound goes wrong.
        if constraint != NumericConstraint.at_least(1):
            stream.reset(mark)
            return None
        constraint = NumericConstraint.exactly(0)
        if scope is PlayerScope.OPPONENT:
            scope = PlayerScope.EACH_OPPONENT

    if scope is PlayerScope.YOU:
        from dataclasses import replace as _replace

        return Condition(
            kind=ConditionKind.CONTROLS_MATCHING,
            filter=_replace(spec, controller=ControllerRelation.YOU, count=None),
            constraint=constraint,
            text="you control ...",
        )
    if spec.controller is not ControllerRelation.ANY:
        stream.reset(mark)
        return None
    return Condition(
        kind=ConditionKind.COMPARE_COUNTS,
        players=PlayerFilter(scope),
        filter=spec,
        constraint=constraint,
        text="a player controls ...",
    )


def _condition_player(stream: Stream):
    """The player a "has ..." condition is about, and how it is quantified.

    Returns the filters to ask and whether one of them is enough. "A player
    has 13 or less life" is you *or* an opponent (CR 102.1), which no single
    player scope says - the scope the noun reader gives "a player" is every
    player, and every player at 13 life is a different condition.
    """
    from ..rules.kernel.query import PlayerFilter, PlayerScope

    if stream.accept_phrase("a player") or stream.accept_phrase("any player"):
        return (PlayerFilter(PlayerScope.YOU), PlayerFilter(PlayerScope.OPPONENT))
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if players is None:
        return None
    if targeted or players.scope not in (
        PlayerScope.YOU,
        PlayerScope.OPPONENT,
        PlayerScope.EACH_OPPONENT,
        PlayerScope.EACH_PLAYER,
    ):
        # "that player", "target player", "defending player": the engine has
        # no binding for who is meant when a condition is asked, so the
        # question would always come out false. Declined rather than inert.
        stream.reset(mark)
        return None
    if players.scope is PlayerScope.EACH_PLAYER and not stream.tokens[mark].lower == "each":
        # "players" and "one or more players" - not a condition shape.
        stream.reset(mark)
        return None
    return (players,)


def _any_player(kind, players, constraint, text):
    """One condition per player filter, joined by OR when there are several."""
    from ..rules.kernel.query import Condition, ConditionKind

    parts = tuple(
        Condition(kind=kind, players=each, constraint=constraint, text=text)
        for each in players
    )
    if len(parts) == 1:
        return parts[0]
    return Condition(kind=ConditionKind.OR, operands=parts, text=text)


def _hand_condition(stream: Stream):
    """"you have seven or more cards in hand", "an opponent has no cards in
    hand", "you have one or fewer cards in your hand"."""
    from ..rules.kernel.query import ConditionKind

    mark = stream.mark()
    players = _condition_player(stream)
    if players is None:
        return None
    if not stream.accept("has", "have"):
        stream.reset(mark)
        return None
    constraint = _condition_quantity(stream)
    if constraint is None or not stream.accept("card", "cards"):
        stream.reset(mark)
        return None
    if not stream.accept("in"):
        stream.reset(mark)
        return None
    stream.accept("your", "their", "his", "her")
    if not stream.accept("hand"):
        stream.reset(mark)
        return None
    return _any_player(
        ConditionKind.CARDS_IN_HAND, players, constraint, "cards in hand"
    )


def _value_condition(stream: Stream):
    """"your devotion to red is less than five", "X is 5 or more", "creatures
    you control have total power 10 or greater", "your life total is less
    than half your starting life total".

    Only quantities whose meaning the value reader states exactly are taken:
    devotion, X, a count, a total among a set, and your life total.
    """
    from ..rules.kernel.query import Condition, ConditionKind, ValueKind
    from .nouns import parse_value

    mark = stream.mark()
    total = _total_power_among(stream)
    if total is not None:
        value = total
    else:
        value = parse_value(stream)
        if value is None:
            return None
        if value.kind not in (
            ValueKind.DEVOTION,
            ValueKind.X,
            ValueKind.COUNT,
            ValueKind.TOTAL_AMONG,
            ValueKind.LIFE_TOTAL,
        ):
            stream.reset(mark)
            return None
        if value.kind is ValueKind.LIFE_TOTAL and stream.tokens[mark].lower != "your":
            stream.reset(mark)
            return None
        if not stream.accept("is", "are"):
            stream.reset(mark)
            return None
    constraint = parse_comparison(stream)
    if constraint is None:
        stream.reset(mark)
        return None
    return Condition(
        kind=ConditionKind.VALUE_COMPARE,
        value=value,
        constraint=constraint,
        text="a value comparison",
    )


def _total_power_among(stream: Stream):
    """"creatures you control have total power 10 or greater"."""
    from ..rules.kernel.query import ValueKind

    mark = stream.mark()
    spec = parse_object_filter(stream)
    if spec is None or spec.count is not None:
        stream.reset(mark)
        return None
    if not stream.accept("have", "has"):
        stream.reset(mark)
        return None
    if not stream.accept("total"):
        stream.reset(mark)
        return None
    if stream.accept("power"):
        inner = ValueKind.POWER
    elif stream.accept("toughness"):
        inner = ValueKind.TOUGHNESS
    elif stream.accept_phrase("mana value"):
        inner = ValueKind.MANA_VALUE
    else:
        stream.reset(mark)
        return None
    return Value(
        kind=ValueKind.TOTAL_AMONG,
        filter=spec,
        operands=(Value(kind=inner, of_affected=True),),
    )


#: Who a "controls more X than" comparison is about, by the phrase it opens
#: with. The comparison is asked of each of them separately.
_COMPARISON_SUBJECTS = (
    ("an opponent controls", PlayerScopeName("OPPONENT")),
    ("that player controls", PlayerScopeName("TARGET_PLAYER")),
    ("each opponent controls", PlayerScopeName("EACH_OPPONENT")),
    ("you control", PlayerScopeName("YOU")),
)


def _controls_more_than(stream: Stream):
    """"an opponent controls more lands than you", "you control more creatures
    than each opponent".

    Two counts compared, which no single count could express. The filter is
    read once and applied to both sides: the thing being counted is the same
    thing, and only whose it is differs.
    """
    from ..rules.kernel.query import (
        Comparison,
        Condition,
        ConditionKind,
        NumericConstraint,
        PlayerFilter,
        PlayerScope,
        Value,
        ValueKind,
    )

    mark = stream.mark()
    for phrase, scope in _COMPARISON_SUBJECTS:
        if stream.accept_phrase(phrase):
            subject = PlayerFilter(getattr(PlayerScope, scope.name))
            break
    else:
        return None

    if stream.accept("more"):
        comparison = Comparison.GT
    elif stream.accept("fewer") or stream.accept("less"):
        comparison = Comparison.LT
    else:
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if not stream.accept("than"):
        stream.reset(mark)
        return None

    other, _ = parse_player_filter(stream)
    if other is None:
        stream.reset(mark)
        return None

    from dataclasses import replace as _replace

    from ..rules.kernel.query import ControllerRelation

    # The other side is counted by *relation* rather than by player filter,
    # because that is how a filter says whose permanents it means.
    relation = (
        ControllerRelation.YOU
        if other.scope is PlayerScope.YOU
        else ControllerRelation.OPPONENT
    )

    return Condition(
        kind=ConditionKind.COMPARE_COUNTS,
        players=subject,
        filter=_replace(spec, controller=ControllerRelation.ANY),
        constraint=NumericConstraint(
            comparison,
            Value(
                kind=ValueKind.COUNT,
                filter=_replace(spec, controller=relation),
            ),
        ),
        text=f"{subject} controls more {spec} than {other}",
    )


def _about_one_object(subject, narrowed, text: str):
    """A test on one named object, by who that object is.

    * "it", "that creature" - whatever the resolution last acted on, asked
      of that object (REMEMBERED_MATCHES). Searching the board for *any*
      object that fits - what counting the filter did - made "if it's
      tapped" true whenever anything at all was tapped.
    * "this creature" - the source, which ``find`` locates wherever it is.
    * "enchanted creature", "equipped creature" and "a creature ..." - some
      object on the battlefield that fits, which a count of one answers.

    Anything else ("creatures you control are ...", "target creature is")
    is not one known object and is declined.
    """
    from ..rules.kernel.query import (
        Condition,
        ConditionKind,
        NumericConstraint,
        ValueKind,
    )

    if subject.remembered:
        return Condition(
            kind=ConditionKind.REMEMBERED_MATCHES,
            filter=replace(narrowed, remembered=True, count=None, zones=frozenset()),
            text=text,
        )
    single = (
        subject.count is not None
        and subject.count.kind is ValueKind.CONSTANT
        and subject.count.constant == 1
        and not subject.up_to
    )
    if subject.source_only or subject.has_attached is not None or single:
        return Condition(
            kind=ConditionKind.CONTROLS_MATCHING,
            filter=replace(narrowed, count=None),
            constraint=NumericConstraint.at_least(1),
            text=text,
        )
    return None


def _is_supertype(stream: Stream):
    """"as long as equipped creature is legendary", "if that creature is a
    Dragon".

    A description of one object rather than a count of many, so it is asked
    as "does this object match" against the filter the sentence already
    built.
    """
    mark = stream.mark()
    subject = parse_object_filter(stream)
    if subject is None:
        stream.reset(mark)
        return None
    if not stream.accept("is", "are"):
        stream.reset(mark)
        return None

    # "is legendary", "is white" - a bare qualifier with no head noun, which
    # the noun reader will not return on its own.
    from .nouns import qualifiers_only

    narrowed = qualifiers_only(stream)
    if narrowed is None:
        stream.reset(mark)
        return None
    # Only the qualifiers carried across are allowed: anything else the
    # description said would be read and then dropped.
    carried = replace(
        ObjectFilter(),
        supertypes_all=narrowed.supertypes_all,
        types_all=narrowed.types_all,
        subtypes_all=narrowed.subtypes_all,
        colors_any=narrowed.colors_any,
    )
    if replace(narrowed, count=None) != carried:
        stream.reset(mark)
        return None

    condition = _about_one_object(
        subject,
        replace(
            subject,
            supertypes_all=subject.supertypes_all | narrowed.supertypes_all,
            types_all=subject.types_all | narrowed.types_all,
            subtypes_all=subject.subtypes_all + narrowed.subtypes_all,
            colors_any=subject.colors_any | narrowed.colors_any,
        ),
        "that object is ...",
    )
    if condition is None:
        stream.reset(mark)
    return condition


def _life_condition(stream: Stream):
    """"an opponent has 10 or less life", "you have 5 or more life", "a
    player has 13 or less life"."""
    from ..rules.kernel.query import Comparison, ConditionKind, NumericConstraint

    mark = stream.mark()
    players = _condition_player(stream)
    if players is None:
        return None
    if not stream.accept("has", "have"):
        stream.reset(mark)
        return None
    amount = parse_value(stream)
    if amount is None:
        stream.reset(mark)
        return None

    if stream.accept_phrase("or less") or stream.accept_phrase("or fewer"):
        comparison = Comparison.LE
    elif stream.accept_phrase("or more") or stream.accept_phrase("or greater"):
        comparison = Comparison.GE
    else:
        # "you have 20 life" - a bare number is not a threshold, and reading
        # it as one would be a guess.
        stream.reset(mark)
        return None

    if not stream.accept("life"):
        stream.reset(mark)
        return None
    return _any_player(
        ConditionKind.LIFE,
        players,
        NumericConstraint(comparison, amount),
        "a life-total condition",
    )


def _counter_condition(stream: Stream):
    """"this creature has four or more +1/+1 counters on it", "it has a
    +1/+1 counter on it", "if it had no counters on it"."""
    from ..rules.kernel.gameobject import GameObject
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    subject = parse_object_filter(stream)
    if subject is None:
        return None
    if not stream.accept("has", "have", "had"):
        stream.reset(mark)
        return None
    constraint = _condition_quantity(stream)
    if constraint is None:
        stream.reset(mark)
        return None

    if stream.accept("counter", "counters"):
        # "counters on it" with no kind named - any counter at all.
        counter = GameObject.ANY_COUNTER
    else:
        counter = _counter_word(stream)
        if counter is None:
            stream.reset(mark)
            return None
    if not stream.accept("on"):
        stream.reset(mark)
        return None
    if not stream.accept("it", "them"):
        if not stream.accept("this"):
            stream.reset(mark)
            return None
        stream.accept(*_SELF_NOUNS_FOR_COUNTERS)

    if subject.source_only or subject.remembered:
        # "This creature has ...", and "it has ..." in a static ability of the
        # creature itself ("has reach as long as it has a +1/+1 counter on
        # it"), where "it" is the source. Asked of the source, as before.
        return Condition(
            kind=ConditionKind.COUNTER_COUNT,
            filter=subject,
            counter_type=counter,
            constraint=constraint,
            text="a counter condition",
        )
    condition = _about_one_object(
        subject,
        replace(subject, has_counter=counter, counter_constraint=constraint),
        "a counter condition",
    )
    if condition is None:
        stream.reset(mark)
    return condition


_SELF_NOUNS_FOR_COUNTERS = (
    "creature", "permanent", "artifact", "enchantment", "land", "card",
    "planeswalker", "equipment", "aura", "vehicle", "saga",
)


def _state_condition(stream: Stream):
    """"<something> is tapped / untapped / attacking / blocking"."""
    mark = stream.mark()
    subject = parse_object_filter(stream)
    if subject is None:
        return None
    if not stream.accept("is", "are"):
        stream.reset(mark)
        return None

    if stream.accept("tapped"):
        narrowed = replace(subject, tapped=True)
    elif stream.accept("untapped"):
        narrowed = replace(subject, tapped=False)
    elif stream.accept("attacking"):
        narrowed = replace(subject, attacking=True)
    elif stream.accept("blocking"):
        narrowed = replace(subject, blocking=True)
    elif stream.accept("blocked"):
        narrowed = replace(subject, blocked=True)
    else:
        stream.reset(mark)
        return None

    condition = _about_one_object(subject, narrowed, "a state condition")
    if condition is None:
        stream.reset(mark)
    return condition


def _theirs(spec):
    from dataclasses import replace

    from ..rules.kernel.query import ControllerRelation

    return replace(spec, controller=ControllerRelation.OPPONENT, count=None)


#: "<who> <did something> [N or more] <noun> this turn" - the events cards ask
#: about after the fact: the verb as it can be written (past tense and
#: participle), the event, whether the question is "how many times" or "how
#: much", and the only nouns the verb may take.
#:
#: The noun is part of the question and has to be one the tally can answer.
#: The game keeps a count per event kind and player, nothing finer, so "cast
#: a spell" can be asked and "cast a noncreature spell" cannot; a noun with
#: any qualifier on it is declined rather than read as the bare noun. Damage
#: is absent on purpose: the tally files damage to a creature under its
#: controller, so "was dealt damage this turn" cannot be told apart from
#: "controls a creature that was".
_THIS_TURN_EVENTS: tuple[tuple[tuple[str, ...], str, bool, tuple[str, ...]], ...] = (
    (("lost", "lose"), "LIFE_LOST", False, ("life",)),
    (("gained",), "LIFE_GAINED", False, ("life",)),
    (("cast",), "CAST_SPELL", True, ("spell", "spells")),
    (("drew", "drawn"), "DREW_CARD", True, ("card", "cards")),
    (("attacked with",), "ATTACKS", True, ("creature", "creatures")),
    (("sacrificed",), "SACRIFICED", True, ("permanent", "permanents")),
    (("discarded",), "DISCARDED", True, ("card", "cards")),
    # "if you attacked this turn" (raid): one attack event per attacking
    # creature, so having attacked at all is a count of at least one.
    (("attacked",), "ATTACKS", True, ()),
)


def _about_the_remembered(stream: Stream):
    """"if it's blue", "if it was a creature", "if its power is 3 or greater".

    All of them ask about the object the sentence before them acted on, which
    is one construct written three ways: a colour, a type, or a
    characteristic. "Was" rather than "is" matters on a dies trigger - the
    creature is in a graveyard by the time the ability resolves - and the
    engine answers from last-known information for exactly that reason.
    """
    from dataclasses import replace as _replace

    from ..rules.kernel.query import Condition, ConditionKind, ObjectFilter

    mark = stream.mark()

    # "its power is 3 or greater" - a characteristic rather than a description.
    if stream.accept("its"):
        for word, field in (("power", "power"), ("toughness", "toughness")):
            if stream.accept(word):
                stream.accept("is", "was")
                constraint = parse_comparison(stream)
                if constraint is None:
                    stream.reset(mark)
                    return None
                spec = ObjectFilter(remembered=True, zones=frozenset())
                return Condition(
                    kind=ConditionKind.REMEMBERED_MATCHES,
                    filter=_replace(spec, **{field: constraint}),
                    text=f"its {word} ...",
                )
        stream.reset(mark)
        return None

    # "it's blue", "it was a creature", "it's a land card".
    if not (
        stream.accept_phrase("it's")
        or stream.accept_phrase("it is")
        or stream.accept_phrase("it was")
        or stream.accept_phrase("that card is")
        or stream.accept_phrase("that creature is")
    ):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        # "it's blue" names no noun, because a test about a known object does
        # not need one.
        spec = parse_description(stream)
    if spec is None:
        stream.reset(mark)
        return None
    # The description came from a noun phrase, so it carries a quantity and a
    # zone it should not: this is a test on one known object, not a search.
    spec = _replace(spec, remembered=True, count=None, zones=frozenset())
    return Condition(
        kind=ConditionKind.REMEMBERED_MATCHES, filter=spec, text="it is ..."
    )


def _happened_this_turn(stream: Stream):
    """"if an opponent lost 2 or more life this turn", "if you've cast two or
    more spells this turn", "if you attacked this turn".

    The trailing "this turn" is what makes it a question about history rather
    than about the board, and the engine keeps a per-turn tally to answer it.
    An amount ("3 or more life") and a count ("two or more spells") are both
    expressible; which one is meant depends on the verb.
    """
    from ..rules.kernel.events import EventKind
    from ..rules.kernel.query import Condition, ConditionKind, PlayerScope

    mark = stream.mark()
    if stream.accept("you've"):
        players = (YOU,)
    else:
        players = _condition_player(stream)
        if players is None:
            stream.reset(mark)
            return None
        stream.accept("has", "have")

    for phrases, event_name, counting, nouns in _THIS_TURN_EVENTS:
        for phrase in phrases:
            look = stream.mark()
            if not stream.accept_phrase(phrase):
                continue
            constraint = _condition_quantity(stream)
            if nouns:
                if not stream.accept(*nouns):
                    stream.reset(look)
                    continue
            elif constraint is not None:
                stream.reset(look)
                continue
            if not stream.accept_phrase("this turn"):
                stream.reset(look)
                continue
            if constraint is not None and constraint.comparison.name == "GE" and (
                constraint.value.constant == 1
            ):
                # "a spell", "one or more cards": it happened at all.
                constraint = None
            if any(p.scope is PlayerScope.EACH_PLAYER for p in players):
                stream.reset(mark)
                return None
            parts = tuple(
                Condition(
                    kind=ConditionKind.EVENT_THIS_TURN,
                    players=each,
                    constraint=constraint,
                    counter_type="count" if counting else "",
                    event_kinds=(int(getattr(EventKind, event_name)),),
                    text=f"{phrase} ... this turn",
                )
                for each in players
            )
            if len(parts) == 1:
                return parts[0]
            return Condition(kind=ConditionKind.OR, operands=parts, text=phrase)

    stream.reset(mark)
    return None


def _opponent_count(stream: Stream):
    """"you have two or more opponents" - the Battlebond land cycle.

    A question about the table, not the board, so none of the object-count
    conditions fit. Multiplayer is the default here and the answer is almost
    always yes, but a four-player game is exactly the setting this simulator
    exists to model, so it is asked properly rather than assumed.
    """
    from ..rules.kernel.query import (
        Condition,
        ConditionKind,
        NumericConstraint,
        PlayerFilter,
        PlayerScope,
    )

    mark = stream.mark()
    if not (stream.accept_phrase("you have") or stream.accept_phrase("you don't have")):
        stream.reset(mark)
        return None
    negated = stream.tokens[mark + 1].lower in ("don't", "do")

    amount = stream.accept_number()
    if amount is None:
        stream.reset(mark)
        return None
    at_least = True
    if stream.accept_phrase("or more"):
        at_least = True
    elif stream.accept_phrase("or fewer") or stream.accept_phrase("or less"):
        at_least = False

    if not stream.accept("opponent", "opponents"):
        stream.reset(mark)
        return None

    condition = Condition(
        kind=ConditionKind.PLAYER_COUNT,
        players=PlayerFilter(PlayerScope.EACH_OPPONENT),
        constraint=(
            NumericConstraint.at_least(amount)
            if at_least
            else NumericConstraint.at_most(amount)
        ),
        text="you have N opponents",
    )
    if negated:
        return Condition(
            kind=ConditionKind.NOT, operands=(condition,), text="you don't have N"
        )
    return condition


def _yours(spec):
    from dataclasses import replace

    from ..rules.kernel.query import ControllerRelation

    return replace(spec, controller=ControllerRelation.YOU, count=None)


@clause("base-pt")
def _base_pt(stream: Stream) -> Effect | None:
    """"Target creature has base power and toughness 4/4 until end of turn."

    Layer 7b, not 7c: it *sets* rather than modifies, so a +1/+1 counter
    applied afterwards still counts and an earlier anthem does not.
    """
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    if not (stream.accept("has", "have", "gets", "get")):
        return None
    if not stream.accept_phrase("base power and toughness"):
        return None
    token = stream.peek()
    if token.kind is not TokenKind.PT:
        return None
    stream.next()
    left, _, right = token.text.partition("/")
    # "have base power and toughness X/X *and gain all creature types*" - the
    # same and-grant tail every other P/T clause reads.
    granted = _also_gains(stream)
    duration = _duration(stream)
    setting = Effect(
        EffectKind.SET_PT,
        targets=targets,
        amount=_pt_value(left),
        amount2=_pt_value(right),
        duration=duration,
        is_targeted=targeted,
        text=f"base power and toughness {token.text}",
    )
    if not granted:
        return setting
    return Effect(
        EffectKind.SEQUENCE,
        children=(
            setting,
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=targets,
                granted_abilities=granted,
                duration=duration,
                is_targeted=targeted,
                text="and gains " + ", ".join(a.keyword or a.text for a in granted),
            ),
        ),
        text="base power and toughness, and abilities",
    )


#: The marker that tells the compiler an ability is characteristic-defining.
#: Carried on the effect's text rather than in a new field because CDA-ness is
#: a property of the *ability* (CR 604.3), and the effect is what the clause
#: has to hand.
CDA_MARK = "cda: "


@clause("defined-pt")
def _defined_pt(stream: Stream) -> Effect | None:
    """"Its power and toughness are each equal to the number of ..." (CR 604.3).

    A characteristic-defining ability, which applies in layer 7a - before any
    other P/T effect, and everywhere the object is, not just on the
    battlefield. Reading it as an ordinary setting effect would put it in
    layer 7b and let a +1/+1 counter be overwritten by it.
    """
    mark = stream.mark()

    targets, targeted = parse_target(stream)
    if targets is None:
        if not stream.accept("its"):
            stream.reset(mark)
            return None
        targets, targeted = SELF, False
    else:
        stream.accept("'s")

    both = stream.accept_phrase("power and toughness")
    if not both and not stream.accept("power"):
        stream.reset(mark)
        return None
    stream.accept("are", "is")
    stream.accept("each")
    if not stream.accept_phrase("equal to"):
        stream.reset(mark)
        return None

    amount = parse_value(stream)
    if amount is None:
        stream.reset(mark)
        return None

    # "*Its power* is equal to X and its toughness is equal to that plus 1"
    # (Tarmogoyf). The two halves are one CDA, and the second is written
    # relative to the first - but it is often absent, because the card prints
    # a real toughness and defines only its power.
    if both:
        toughness = amount
    else:
        toughness = _defined_toughness(stream, amount)
        if toughness is None:
            toughness = Value(kind=ValueKind.UNCHANGED)

    return Effect(
        EffectKind.SET_PT,
        targets=targets,
        amount=amount,
        amount2=toughness,
        duration=Duration.PERMANENT,
        is_targeted=targeted,
        text=f"{CDA_MARK}power and toughness equal to {amount}",
    )


def _defined_toughness(stream: Stream, power: Value) -> Value | None:
    """The "and its toughness is equal to that plus 1" half."""
    from ..rules.kernel.query import ValueKind

    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("and"):
        stream.reset(mark)
        return None
    stream.accept("its")
    if not stream.accept("toughness"):
        stream.reset(mark)
        return None
    stream.accept("is", "are")
    if not stream.accept_phrase("equal to"):
        stream.reset(mark)
        return None

    # "that plus 1" - "that" is the power just read, so the toughness is an
    # arithmetic node over it rather than an independent count.
    if stream.accept("that"):
        if stream.accept("plus"):
            extra = parse_value(stream) or Value.of(1)
            return Value(kind=ValueKind.SUM, operands=(power, extra))
        if stream.accept("minus"):
            extra = parse_value(stream) or Value.of(1)
            return Value(kind=ValueKind.DIFFERENCE, operands=(power, extra))
        return power

    other = parse_value(stream)
    if other is None:
        stream.reset(mark)
        return None
    return other


@clause("becomes-type")
def _becomes_type(stream: Stream) -> Effect | None:
    """"This creature becomes a Human Warrior with base power and toughness 3/3."

    Layer 4 plus, usually, layer 7b. CR 205.1b: becoming a type *adds* it
    unless the card says "in addition to its other types" is absent - the
    common templating replaces creature types and keeps card types, which is
    what ADD_TYPE with subtypes expresses.
    """
    targets, targeted = parse_target(stream)
    if targets is None:
        return None

    # "Enchanted creature *loses all abilities and* is a green Elk creature
    # with base power and toughness 3/3." Lignify and its cycle put the layer
    # 6 removal first and use "is" rather than "becomes"; the clause read
    # neither, so the whole family failed.
    stripped = bool(
        stream.accept_phrase("loses all abilities and")
        or stream.accept_phrase("lose all abilities and")
    )
    if not stream.accept("becomes", "become") and not (
        stripped and stream.accept("is", "are")
    ):
        return None
    spec = parse_object_filter(stream)
    if spec is None:
        return None

    effects = []
    if stripped:
        effects.append(
            Effect(
                EffectKind.REMOVE_ABILITIES,
                targets=targets,
                keywords=("*",),
                duration=Duration.PERMANENT,
                is_targeted=targeted,
                text="loses all abilities",
            )
        )
    effects += [
        Effect(
            EffectKind.ADD_TYPE,
            targets=targets,
            types=spec.types_all,
            keywords=spec.subtypes_all,
            duration=Duration.PERMANENT,
            is_targeted=targeted,
            text="becomes",
        )
    ]
    if stream.accept_phrase("with base power and toughness"):
        token = stream.peek()
        if token.kind is not TokenKind.PT:
            return None
        stream.next()
        left, _, right = token.text.partition("/")
        effects.append(
            Effect(
                EffectKind.SET_PT,
                targets=targets,
                amount=_pt_value(left),
                amount2=_pt_value(right),
                duration=Duration.PERMANENT,
                # The same "target" as the type change: without it "up to one
                # other target creature ... becomes a Lizard with base power
                # and toughness 4/4" set the base of whichever creature the
                # filter found, not the one targeted (CR 115.1).
                is_targeted=targeted,
                text=f"base {token.text}",
            )
        )

    # "and has indestructible", "and it loses all other abilities, card types,
    # and creature types" - the tails the transformation cycle writes. The
    # second says again what the leading "loses all abilities" already did.
    granted = _also_gains(stream)
    if granted:
        effects.append(
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=targets,
                granted_abilities=granted,
                duration=Duration.PERMANENT,
                is_targeted=targeted,
                text="and has " + ", ".join(a.keyword or a.text for a in granted),
            )
        )
    look = stream.mark()
    stream.skip_punct(",")
    if stream.accept("and"):
        stream.accept("it")
        if not (
            stream.accept_phrase(
                "loses all other abilities, card types, and creature types"
            )
            or stream.accept_phrase("loses all other abilities")
            or stream.accept_phrase("loses all other card types")
        ):
            stream.reset(look)
    else:
        stream.reset(look)

    duration = _duration(stream)
    return Effect(
        EffectKind.SEQUENCE,
        children=tuple(
            effect if duration is Duration.PERMANENT else _with_duration(effect, duration)
            for effect in effects
        ),
        text="becomes",
    )


def _with_duration(effect: Effect, duration: Duration) -> Effect:
    from dataclasses import replace

    return replace(effect, duration=duration)


@clause("costs-less")
def _costs_less(stream: Stream) -> Effect | None:
    """"This spell costs {1} less to cast." (CR 601.2f).

    A cost reduction, not a discount applied afterwards - the spell's mana
    value is unchanged, which matters for everything that asks.
    """
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost, UnknownManaSymbol

    targets, _ = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("costs", "cost"):
        return None

    symbols = []
    while stream.peek().kind is TokenKind.SYMBOL:
        symbols.append(stream.next().text)
    amount = parse_value(stream) if not symbols else None
    if not (symbols or amount):
        return None

    reduction = 0
    if symbols:
        try:
            reduction = ManaCost.parse("".join(symbols)).mana_value
        except UnknownManaSymbol:
            return None
    # The word decides the sign, and it was being read and thrown away:
    # ``sign`` was hardcoded to 1, so every cost *reducer* in the format was
    # compiled as a cost *increase*. Urza's Incubator made creature spells
    # cost two more. The engine's convention is negative-is-cheaper, and both
    # cost_reductions and cost_increases read it that way.
    if stream.accept("less"):
        sign = -1
    elif stream.accept("more"):
        sign = 1
    else:
        return None
    stream.accept_phrase("to cast")
    stream.accept_phrase("to activate")

    return Effect(
        EffectKind.MODIFY_COST,
        targets=targets,
        amount=Value.of(sign * (reduction or (amount.constant if amount else 0))),
        duration=Duration.PERMANENT,
        text="costs less to cast",
    )


#: The engine's own spelling of the attack requirement, checked by
#: ``combat._must_attack``. A string rather than an ``Act`` because a
#: requirement is not a prohibition and the two live in different mechanisms.
ATTACKS_IF_ABLE = "Attacks each combat if able"


@clause("attacks-if-able")
def _attacks_if_able(stream: Stream) -> Effect | None:
    """"This creature attacks each combat if able." - a requirement (CR 508.1d).

    Requirements are not restrictions, and this clause used to emit one: an
    ``Act.ATTACK`` prohibition, which is the *opposite* card. A creature that
    must attack every combat became a creature that could never attack, and
    nothing downstream could tell, because a well-formed restriction is
    exactly what a "can't attack" card produces.

    ``Restriction`` has no requirement flag and should not grow one - CR 508.1d
    makes requirements a separate step of the declaration, which combat.py
    already implements by looking for this keyword on the attacker. So the
    grammar asks for the thing the engine already enforces.
    """
    targets, _ = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("attacks", "attack"):
        return None
    stream.accept_phrase("each combat")
    stream.accept_phrase("each turn")
    if not stream.accept_phrase("if able"):
        return None
    return Effect(
        EffectKind.GRANT_ABILITY,
        targets=targets,
        keywords=(ATTACKS_IF_ABLE,),
        duration=Duration.PERMANENT,
        text="attacks each combat if able",
    )


def _complement(spec: ObjectFilter) -> ObjectFilter | None:
    """"creatures with flying" -> "creatures without flying", or ``None``.

    ``Restriction.counterpart`` forbids the act when the other participant
    *matches* it, so a permission - "can block only X" - has to be written as
    the prohibition "can't block anything that is not X". That needs the
    complement of the filter, and ``ObjectFilter`` can only express the
    complement of one constraint at a time: there is no general negation, and
    inventing one by hand is how "can block only creatures with flying" became
    "can't block creatures with flying", which is the same card upside down.

    So exactly one constraint is inverted and everything else declines. The
    creature type itself is dropped first rather than inverted: the counterpart
    of a block is always an attacking creature, so "creature" narrows nothing.
    """
    from ..rules.kernel.enums import CardType as _CardType

    reduced = replace(spec, types_all=spec.types_all & ~_CardType.CREATURE)
    blank = ObjectFilter(zones=reduced.zones)

    for field, inverse in (
        ("has_keyword", "lacks_keyword"),
        ("subtypes_any", "subtypes_none"),
        ("subtypes_all", "subtypes_none"),
        ("named", "not_named"),
        ("types_all", "types_none"),
        ("types_any", "types_none"),
        ("colors_all", "colors_none"),
        ("colors_any", "colors_none"),
    ):
        value = getattr(reduced, field)
        if not value:
            continue
        # Every *other* constraint has to be at its default, or the complement
        # would quietly widen: "only green creatures you control" is not
        # "anything that is not green".
        if reduced != replace(blank, **{field: value}):
            return None
        return replace(blank, **{inverse: value})
    return None


@clause("can-block-only")
def _can_block_only(stream: Stream) -> Effect | None:
    """"This creature can block only creatures with flying."

    A restriction on what it may block, so everything *else* becomes illegal -
    the inverse of how it reads, and the inversion has to be done to the
    filter rather than assumed by the reader. See ``_complement``: where the
    engine cannot express the complement, this declines and the ability fails
    to parse, which leaves the creature blocking freely. That is a worse
    blocker than the card prints and a better one than the inverted reading
    gave, and unlike the inverted reading it is visible in the coverage report.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    targets, _ = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("can"):
        return None
    if not stream.accept("block"):
        return None
    if not stream.accept("only"):
        return None
    allowed = parse_object_filter(stream)
    if allowed is None:
        return None
    forbidden = _complement(allowed)
    if forbidden is None:
        return None
    return Effect(
        EffectKind.RESTRICTION,
        targets=targets,
        restrictions=(
            Restriction(
                act=Act.BLOCK,
                subject=targets,
                counterpart=forbidden,
                text="can't block anything it isn't allowed to block",
            ),
        ),
        duration=Duration.PERMANENT,
        text="can block only",
    )


@clause("loses-keyword")
def _loses_keyword(stream: Stream) -> Effect | None:
    """"Target creature loses flying until end of turn." - layer 6, removal."""
    from ..rules.cr700_additional_rules.keywords import is_known

    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    if not stream.accept("loses", "lose"):
        return None
    # "loses your choice of flying, first strike, or trample" and "loses
    # first strike or swampwalk" take away *one* of the list, chosen as the
    # effect resolves. Read as a list both lost every keyword named - a
    # stronger card - so the choice is declined until there is an opcode
    # that chooses.
    if stream.at_phrase("your choice of"):
        return None

    names: list[str] = []
    while True:
        token = stream.peek()
        if token.kind is not TokenKind.WORD:
            break
        pair = f"{token.text} {stream.peek(1).text}".strip()
        if is_known(pair):
            stream.next()
            stream.next()
            names.append(pair)
        elif is_known(token.text):
            stream.next()
            names.append(token.text)
        else:
            break
        look = stream.mark()
        stream.skip_punct(",")
        if stream.accept("or"):
            return None
        stream.reset(look)
        stream.skip_punct(",")
        stream.accept("and")

    if not names:
        return None
    duration = _duration(stream)
    return Effect(
        EffectKind.REMOVE_ABILITIES,
        targets=targets,
        keywords=tuple(names),
        duration=duration,
        is_targeted=targeted,
        text="loses " + ", ".join(names),
    )


# ---------------------------------------------------------------------------
# "As ..." clauses, paying, and revealing
# ---------------------------------------------------------------------------


@clause("as-enters")
def _as_enters(stream: Stream) -> Effect | None:
    """"As this enchantment enters, choose a creature type." (CR 614.1b).

    An "as ... enters" effect is a replacement, made *as* the permanent
    enters rather than after - so nothing can respond and the choice is
    already made by the time anything sees the permanent.
    """
    if not stream.accept("as"):
        return None
    # "As *it* enters" - the pronoun form, used when an earlier sentence on
    # the same card already named the permanent. Read as the source, which is
    # what it refers to.
    if stream.accept("it"):
        pass
    else:
        subject = parse_object_filter(stream)
        if subject is None or not subject.source_only:
            return None
    if not stream.accept("enters", "enter"):
        return None
    stream.skip_punct(",")
    inner = parse_effects(stream)
    if inner is None:
        return None
    return Effect(
        EffectKind.SEQUENCE,
        children=tuple(inner),
        duration=Duration.PERMANENT,
        text="as this enters",
    )


@clause("choose-a-type")
def _choose_a_type(stream: Stream) -> Effect | None:
    """"Choose a creature type." / "Choose a color."

    The choice is recorded on the object for later sentences to read; the
    engine's mode machinery already stores exactly that.
    """
    if not stream.accept("choose"):
        return None
    stream.accept("a", "an")
    # "choose artifact, creature, enchantment, instant, or sorcery" - the
    # menu is written out instead of named. It is still a card type being
    # chosen, and which types are on the menu changes nothing the engine
    # records.
    look = stream.mark()
    listed = _listed_card_types(stream)
    if listed:
        return Effect(
            EffectKind.CHOOSE_QUALITY,
            players=YOU,
            keywords=("card type",),
            text="choose a card type",
        )
    stream.reset(look)

    if stream.accept_phrase("creature type"):
        kind = "creature type"
    elif stream.accept_phrase("card type"):
        kind = "card type"
    elif stream.accept_phrase("land type"):
        kind = "land type"
    elif stream.accept("color", "colour"):
        kind = "color"
        # "choose a color other than blue" - the exclusion narrows the menu
        # and nothing later reads it, so it is consumed rather than modelled.
        look = stream.mark()
        if stream.accept("other") and stream.accept("than"):
            if not _colour_word(stream):
                stream.reset(look)
        else:
            stream.reset(look)
    else:
        return None

    # This used to be a CHOOSE_MODE with no children: an effect that recorded
    # nothing, so "the chosen type" in a later sentence had nothing to read.
    return Effect(
        EffectKind.CHOOSE_QUALITY,
        players=YOU,
        keywords=(kind,),
        text=f"choose a {kind}",
    )


def _listed_card_types(stream: Stream) -> bool:
    """A written-out menu of card types: "artifact, creature, or enchantment"."""
    from .nouns import TYPE_WORDS

    seen = 0
    while True:
        stream.accept("or", "and")
        word = stream.peek().lower
        if word not in TYPE_WORDS:
            break
        stream.next()
        seen += 1
        look = stream.mark()
        stream.skip_punct(",")
        if not stream.at("or", "and") and stream.peek().lower not in TYPE_WORDS:
            stream.reset(look)
            break
    return seen >= 2


def _colour_word(stream: Stream) -> bool:
    """One colour name, for the "other than blue" tail."""
    from .nouns import COLOUR_WORDS

    if stream.peek().lower in COLOUR_WORDS:
        stream.next()
        return True
    return False


@clause("pay")
def _pay(stream: Stream) -> Effect | None:
    """"Pay {2}." and "Pay 3 life." as *effects*.

    Distinct from a cost: this appears inside a resolution - "you may pay
    {2}. If you do, ..." - where it is an action taken, not a price of
    activation.

    This used to emit MODIFY_COST, which is the opcode for *changing what
    other spells cost*. That turned "you may pay {1}" into "spells cost {1}
    more" - a completely different card that still ran, and the reason
    Rhystic Study taxed the table instead of drawing a card.
    """
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost, UnknownManaSymbol
    from ..rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind

    if not stream.accept("pay", "pays"):
        return None

    symbols = _mana_run(stream)
    if symbols:
        try:
            mana = ManaCost.parse("".join(symbols))
        except UnknownManaSymbol:
            return None
        return Effect(
            EffectKind.PAY_COST,
            players=YOU,
            pay_cost=Cost((CostComponent(CostKind.MANA, mana=mana),)),
            text="pay " + "".join(symbols),
        )

    amount = parse_value(stream)
    if amount is not None and stream.accept("life"):
        return Effect(
            EffectKind.LOSE_LIFE, players=YOU, amount=amount, text="pay life"
        )
    return None


@clause("reveal-hand")
def _reveal_hand(stream: Stream) -> Effect | None:
    """"Target player reveals their hand."."""
    players, targeted = parse_player_filter(stream)
    if players is None:
        return None
    if not stream.accept("reveals", "reveal"):
        return None
    if not (stream.accept_phrase("their hand") or stream.accept_phrase("your hand")):
        return None
    return Effect(
        EffectKind.REVEAL,
        players=players,
        from_zone=Zone.HAND,
        is_targeted=targeted,
        text="reveal hand",
    )


@clause("reveal-top")
def _reveal_top(stream: Stream) -> Effect | None:
    """"Reveal the top four cards of your library." (CR 701.20).

    The same instruction as looking at them, shown to everyone: the cards stay
    where they are and become the pile the next sentences choose among. It
    used to be read by a clause that could not get past the word "top", and
    what did read it produced a reveal of *this* object, so "reveal the top
    card of your library. If it's a creature card, put it into your hand"
    asked its question about the wrong card.
    """
    return _top_cards_pile(stream, ("reveal", "reveals"), reveal=True)


@clause("sacrifice-it")
def _sacrifice_it(stream: Stream) -> Effect | None:
    """"Sacrifice it." / "Sacrifice this creature." - the bare form.

    Distinct from the targeted form because "it" refers to whatever the
    previous sentence acted on, which the resolution remembers.
    """
    if not stream.accept("sacrifice", "sacrifices"):
        return None
    if not stream.accept("it", "them"):
        return None
    return Effect(EffectKind.SACRIFICE, targets=SELF, text="sacrifice it")


# ---------------------------------------------------------------------------
# Library manipulation
# ---------------------------------------------------------------------------


@clause("look-at-top")
def _look_at_top(stream: Stream) -> Effect | None:
    """"Look at the top four cards of your library."

    This was read as SCRY, which it is not: scrying *moves* the cards it
    rejects to the bottom (CR 701.22a), and the default agent bottoms
    anything it does not like, so "look at the top card" dug through the
    library, and Impulse's "put one of them into your hand" then took
    whatever the resolution happened to remember. Looking moves nothing. It
    sets the cards aside as a pile (``EffectKind.LOOK_AT_TOP``) that "one of
    them", "a creature card from among them" and "the rest" choose among.
    """
    return _top_cards_pile(stream, ("look", "looks"), reveal=False)


def _top_cards_pile(
    stream: Stream, verbs: tuple[str, ...], *, reveal: bool
) -> Effect | None:
    """[subject] look at / reveal "the top N cards of <whose> library".

    Whose library is said three ways, and each is kept: "your library" is
    the controller's; "target player's library" / "target opponent's
    library" is a player chosen as the ability is put on the stack (CR
    115.1); and a named subject's "their library" is that player's. Anything
    else - "each player's library", "that player's library" - names a player
    this cannot pin down and is left unread.
    """
    mark = stream.mark()
    subject, subject_targeted = parse_player_filter(stream)
    if not stream.accept(*verbs):
        stream.reset(mark)
        return None
    if not reveal and not stream.accept("at"):
        stream.reset(mark)
        return None
    if not stream.accept_phrase("the top"):
        stream.reset(mark)
        return None
    amount = Value.of(1)
    if stream.accept("card"):
        pass
    else:
        amount = parse_value(stream)
        if amount is None or not stream.accept("cards"):
            stream.reset(mark)
            return None
    if not stream.accept("of"):
        stream.reset(mark)
        return None

    players, targeted = None, False
    if stream.accept_phrase("your library"):
        players = YOU
        if subject is not None and subject.scope is not PlayerScope.YOU:
            stream.reset(mark)
            return None
    elif stream.accept_phrase("target player's library"):
        players, targeted = PlayerFilter(PlayerScope.TARGET_PLAYER), True
    elif stream.accept_phrase("target opponent's library"):
        players, targeted = PlayerFilter(PlayerScope.TARGET_OPPONENT), True
    elif stream.accept_phrase("their library") or stream.accept_phrase(
        "his or her library"
    ):
        if subject is None or subject.scope not in (
            PlayerScope.TARGET_PLAYER,
            PlayerScope.TARGET_OPPONENT,
            PlayerScope.REFERRED_PLAYER,
        ):
            stream.reset(mark)
            return None
        players, targeted = subject, subject_targeted
    else:
        stream.reset(mark)
        return None
    if subject is not None and players is not subject and subject.scope is not (
        PlayerScope.YOU
    ):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.LOOK_AT_TOP,
        players=players,
        is_targeted=targeted,
        amount=amount,
        keywords=("reveal",) if reveal else (),
        text="reveal the top" if reveal else "look at the top",
    )


@clause("exile-top")
def _exile_top(stream: Stream) -> Effect | None:
    """"Exile the top card of your library." - impulse draw's first half."""
    players, _ = parse_player_filter(stream)
    if not stream.accept("exile", "exiles"):
        return None
    stream.accept("the")
    if not stream.accept("top"):
        return None
    amount = parse_value(stream) or Value.of(1)
    if not stream.accept("card", "cards"):
        return None
    if not (
        stream.accept_phrase("of your library")
        or stream.accept_phrase("of their library")
        or stream.accept_phrase("of each player's library")
    ):
        return None
    return Effect(
        EffectKind.EXILE,
        players=players or YOU,
        from_zone=Zone.LIBRARY,
        amount=amount,
        text="exile from the top of a library",
    )


@clause("put-on-library")
def _put_on_library(stream: Stream) -> Effect | None:
    """"Put it on top of your library." / "... on the bottom of your library."."""
    if not stream.accept("put", "puts"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None and not stream.accept("it", "them"):
        return None
    if not stream.accept("on"):
        return None
    stream.accept("the")
    # Top or bottom is the whole difference between Condemn and a card that
    # hands the attacker back as next turn's draw; it was accepted and dropped.
    bottom = stream.at("bottom")
    if not stream.accept("top", "bottom"):
        return None
    if not (
        stream.accept_phrase("of your library")
        or stream.accept_phrase("of their library")
        or stream.accept_phrase("of its owner's library")
    ):
        return None
    order = _library_order_words(stream)
    return Effect(
        EffectKind.PUT_ON_LIBRARY,
        targets=targets,
        players=YOU,
        is_targeted=targeted,
        keywords=("bottom", *order) if bottom else order,
        text="put on library",
    )


# ---------------------------------------------------------------------------
# Mana of any colour
# ---------------------------------------------------------------------------


@clause("add-any-colour")
def _add_any_colour(stream: Stream) -> Effect | None:
    """"Add one mana of any color." and "Add two mana in any combination".

    CR 106.6: the colour is chosen as the ability resolves, so the effect
    carries no colour and the pool records whatever was picked. Modelled as
    every colour being available, which is what "any" means at the point the
    payment is solved.
    """

    if not stream.accept("add", "adds"):
        return None

    # "Add *an amount of* mana of that color equal to your devotion" puts the
    # quantity after the colour instead of before it. Checked first, because
    # "an" tokenizes as the number one and the ordinary reader below would
    # take it as the amount and then fail on the word "amount".
    scaled = _amount_of_mana(stream)
    if scaled is not None:
        return scaled

    amount = parse_value(stream)
    if amount is None:
        return None
    if not stream.accept("mana"):
        return None

    # CR 903.4: "in your commander's color identity" narrows the menu, and
    # the engine does the narrowing; the tail is otherwise swallowed with the
    # other colour tails, which offered Command Tower's owner all five.
    mark = stream.mark()
    in_identity = bool(
        stream.accept_phrase("of any color")
        and (
            stream.accept_phrase("in your commander's color identity")
            or stream.accept_phrase("in your commanders' color identity")
        )
    )
    stream.reset(mark)

    colours = _mana_colour_source(stream)
    if colours is None:
        return None

    return Effect(
        EffectKind.ADD_MANA,
        players=YOU,
        amount=amount,
        colors=colours,
        colors_in_commander_identity=in_identity,
        text="add mana of any color",
    )


# ---------------------------------------------------------------------------
# Control, regeneration, prevention
# ---------------------------------------------------------------------------


@clause("gain-control")
def _gain_control(stream: Stream) -> Effect | None:
    """"Gain control of target creature until end of turn." (CR 613.1b)."""
    if not stream.accept("gain", "gains"):
        return None
    if not stream.accept("control"):
        return None
    if not stream.accept("of"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    duration = _duration(stream)
    return Effect(
        EffectKind.GAIN_CONTROL,
        targets=targets,
        duration=duration,
        is_targeted=targeted,
        text="gain control",
    )


@clause("regenerate")
def _regenerate(stream: Stream) -> Effect | None:
    """"Regenerate this creature." (CR 701.15).

    A replacement shield, not a heal: the next time it would be destroyed
    this turn, it taps, clears damage and is removed from combat instead.
    """
    if not stream.accept("regenerate"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    return Effect(
        EffectKind.REGENERATE,
        targets=targets,
        is_targeted=targeted,
        text="regenerate",
    )


@clause("copy")
def _copy(stream: Stream) -> Effect | None:
    """"Copy it", "Copy target instant or sorcery spell", "Copy that spell".

    A spell on the stack and a permanent on the battlefield are copied by
    different opcodes (CR 707.10 vs 707.2), and the noun phrase says which:
    anything scoped to the stack is a spell copy. Getting this backwards would
    put a copy of a spell onto the battlefield.
    """
    mark = stream.mark()
    if not stream.accept("copy", "copies"):
        stream.reset(mark)
        return None

    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None

    # "You may choose new targets for the copy" is a rider on the copy, and
    # the engine's copy already offers the choice.
    look = stream.mark()
    if stream.accept("you") and stream.accept("may"):
        if not stream.accept_phrase("choose new targets for the copy"):
            stream.reset(look)
    else:
        stream.reset(look)

    on_stack = targets.zones == frozenset({Zone.STACK}) or targets.remembered
    return Effect(
        EffectKind.COPY_SPELL if on_stack else EffectKind.COPY_PERMANENT,
        targets=targets,
        is_targeted=targeted,
        players=YOU,
        text="copy",
    )


@clause("ability-cost-change")
def _ability_cost_change(stream: Stream) -> Effect | None:
    """"This ability costs {1} less to activate for each legendary creature
    you control." (the Kamigawa channel lands, and their cycle.)

    A cost modification aimed at *this ability* rather than at spells, which
    is why it cannot reuse the spell-cost clause: the thing being made cheaper
    is the ability the sentence sits in.
    """
    mark = stream.mark()
    if not stream.accept_phrase("this ability costs") and not (
        stream.accept_phrase("this spell costs")
    ):
        stream.reset(mark)
        return None

    symbols = _mana_run(stream)
    if not symbols:
        stream.reset(mark)
        return None
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost, UnknownManaSymbol

    try:
        cost = ManaCost.parse("".join(symbols))
    except UnknownManaSymbol:
        stream.reset(mark)
        return None

    if stream.accept("less"):
        sign = -1
    elif stream.accept("more"):
        sign = 1
    else:
        stream.reset(mark)
        return None
    stream.accept_phrase("to activate")
    stream.accept_phrase("to cast")

    # "for each legendary creature you control" scales the reduction.
    per = None
    look = stream.mark()
    if stream.accept_phrase("for each"):
        per = parse_object_filter(stream)
        if per is None:
            stream.reset(look)

    amount = Value.of(sign * cost.mana_value)
    if per is not None:
        amount = Value(
            kind=ValueKind.PRODUCT,
            operands=(amount, Value(kind=ValueKind.COUNT, filter=per)),
        )
    return Effect(
        EffectKind.MODIFY_COST,
        targets=SELF,
        amount=amount,
        duration=Duration.PERMANENT,
        text="this ability costs less",
    )


@clause("enter-as-copy")
def _enter_as_copy(stream: Stream) -> Effect | None:
    """"You may have this creature enter as a copy of any creature on the
    battlefield." (CR 706.2, applied as an entry replacement.)

    Clone, Spark Double, Phyrexian Metamorph and the rest of a large family.
    The choice is made as the permanent enters, so it is a replacement rather
    than a triggered ability - nothing ever sees the original.
    """
    mark = stream.mark()
    stream.accept("you")
    stream.accept("may")
    if not stream.accept("have"):
        stream.reset(mark)
        return None
    if not stream.accept("this"):
        stream.reset(mark)
        return None
    stream.accept(*SELF_NOUNS)
    if not stream.accept("enter", "enters"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("as a copy of") or stream.accept_phrase("as a copy")
    ):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    stream.accept_phrase("on the battlefield")
    stream.skip_punct(",")
    # "except it's an artifact in addition to its other types", "except it
    # enters with an additional +1/+1 counter". These change what the copy
    # becomes and the copy machinery does not model them, so they are read to
    # the end of the sentence and dropped - the alternative is failing the
    # whole card, which loses the copy as well as the exception.
    if stream.accept("except"):
        while not stream.done and stream.peek().text not in (".", ";"):
            stream.next()

    return Effect(
        EffectKind.COPY_PERMANENT,
        targets=spec,
        players=YOU,
        text="enters as a copy",
    )


@clause("retarget")
def _retarget(stream: Stream) -> Effect | None:
    """"Change the target of target spell or ability with a single target."

    And "You may choose new targets for target spell or ability", which is the
    same opcode asked politely.
    """
    mark = stream.mark()
    optional = False
    if stream.accept("you"):
        if not stream.accept("may"):
            stream.reset(mark)
            return None
        optional = True

    if stream.accept_phrase("change the target of") or stream.accept_phrase(
        "change the targets of"
    ):
        pass
    elif stream.accept_phrase("choose new targets for"):
        pass
    else:
        stream.reset(mark)
        return None

    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None
    # "target spell *or ability*". An ability on the stack is not an object
    # the noun grammar reads, so the noun phrase stops at "spell" and leaves
    # the rest - which failed every card in this family.
    look = stream.mark()
    if stream.accept("or"):
        if not stream.accept("ability", "abilities"):
            stream.reset(look)

    stream.accept_phrase("with a single target")
    effect = Effect(
        EffectKind.CHANGE_TARGETS,
        targets=targets,
        is_targeted=targeted,
        players=YOU,
        text="change targets",
    )
    if not optional:
        return effect
    return Effect(
        EffectKind.OPTIONAL, children=(effect,), players=YOU, text="you may"
    )


@clause("trigger-rider")
def _trigger_rider(stream: Stream) -> Effect | None:
    """"This ability triggers only once each turn." (CR 603.2f.)

    A sentence about the ability it sits in rather than about the board, so it
    produces no game action - but it has to be *read*, because an unread
    sentence fails the whole ability and takes the trigger with it. Whether the
    limit is enforced is the trigger's business; the compiler lifts it off this
    marker.
    """
    mark = stream.mark()
    if not stream.accept_phrase("this ability triggers only"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("once each turn")
        or stream.accept_phrase("once a turn")
    ):
        stream.reset(mark)
        return None
    return Effect(EffectKind.NOTHING, text=ONCE_EACH_TURN_MARK)


#: Marks the effect that carries a "triggers only once each turn" rider, so
#: the compiler can lift it onto the trigger where it belongs.
ONCE_EACH_TURN_MARK = "once each turn"


@clause("exile-graveyard")
def _exile_graveyard(stream: Stream) -> Effect | None:
    """"Exile target player's graveyard." - the whole zone, not a card in it.

    Bojuka Bog and the graveyard-hate family. The noun phrase names a *zone*
    belonging to a player, which the object grammar cannot express: it reads
    descriptions of objects, and "their graveyard" is a container.
    """
    mark = stream.mark()
    if not stream.accept("exile"):
        stream.reset(mark)
        return None

    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("graveyard", "graveyards"):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.EXILE,
        targets=ObjectFilter(zones=frozenset({Zone.GRAVEYARD})),
        players=players,
        is_targeted=targeted,
        text="exile a graveyard",
    )


@clause("that-much")
def _that_much(stream: Stream) -> Effect | None:
    """"You lose life equal to that much", "target opponent loses that much
    life".

    "That much" refers to a quantity the sentence before it produced - the
    life gained, the damage dealt - which the resolution already remembers.
    Reanimate and the whole drain family are written this way.
    """
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None

    if stream.accept("lose", "loses"):
        kind = EffectKind.LOSE_LIFE
    elif stream.accept("gain", "gains"):
        kind = EffectKind.GAIN_LIFE
    else:
        stream.reset(mark)
        return None

    # "loses that much life" and "lose life equal to that much" both occur.
    if stream.accept_phrase("that much life"):
        pass
    elif stream.accept("life") and stream.accept_phrase("equal to that much"):
        pass
    else:
        stream.reset(mark)
        return None

    return Effect(
        kind,
        players=players,
        amount=Value(kind=ValueKind.COUNT, filter=ObjectFilter(remembered=True)),
        is_targeted=targeted,
        text="that much life",
    )


@clause("for-each-colour")
def _for_each_colour(stream: Stream) -> Effect | None:
    """"For each color among permanents you control, add one mana of that
    color." (Bloom Tender, Faeburrow Elder and their family.)

    One mana per colour *present*, which is neither a fixed amount nor a count
    of permanents - two green permanents still make one green mana.
    """
    from ..rules.kernel.query import YOU

    mark = stream.mark()
    if not stream.accept_phrase("for each color among") and not (
        stream.accept_phrase("for each colour among")
    ):
        stream.reset(mark)
        return None
    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    if not stream.accept("add", "adds"):
        stream.reset(mark)
        return None
    stream.accept("one", "a", "an")
    stream.accept("mana")
    if not _mana_colour_source(stream):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.ADD_MANA,
        players=YOU,
        amount=Value(kind=ValueKind.COLOURS_AMONG, filter=spec),
        colors=_any_colour(),
        text="one mana of each colour present",
    )


@clause("play-from-zone")
def _play_from_zone(stream: Stream) -> Effect | None:
    """"You may play lands from the top of your library." (CR 118.6, 601.3.)

    A standing permission rather than an action: Oracle of Mul Daya and
    Bolas's Citadel let you play from somewhere other than your hand for as
    long as they are around.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    stream.accept("may")
    if not stream.accept("play", "cast"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if not stream.accept("from"):
        stream.reset(mark)
        return None

    origin = parse_object_filter(stream)
    if origin is None or not origin.from_top:
        stream.reset(mark)
        return None

    from dataclasses import replace as _replace

    scoped = _replace(spec, zones=origin.zones, from_top=origin.from_top)
    return Effect(
        EffectKind.PERMISSION,
        targets=scoped,
        players=players or YOU,
        restrictions=(
            Restriction(
                act=Act.PLAY_LAND if spec.types_all else Act.CAST_SPELL,
                subject=scoped,
                players=players or YOU,
                text="play from the top of your library",
            ),
        ),
        text="may play from elsewhere",
    )


@clause("in-addition-to-types")
def _in_addition_to_types(stream: Stream) -> Effect | None:
    """"Each land is a Swamp in addition to its other land types." (CR 205.1b.)

    Layer 4 type addition, written as a copula rather than as a verb -
    "*is* a Swamp", not "becomes a Swamp" - which is why the "becomes" clause
    never saw it. Urborg, Yavimaya, Dryad of the Ilysian Grove, Maskwood Nexus
    and every "is an X in addition" line share this shape.
    """
    mark = stream.mark()
    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None

    if not stream.accept("is", "are", "becomes", "become"):
        stream.reset(mark)
        return None

    # "This creature is *the chosen type* in addition to its other types."
    # The type is not named on the card - it was chosen as the permanent
    # entered - so there is no noun phrase here to read, only a reference.
    if stream.accept_phrase("the chosen type"):
        if not (
            stream.accept_phrase("in addition to its other types")
            or stream.accept_phrase("in addition to its other creature types")
        ):
            stream.reset(mark)
            return None
        return Effect(
            EffectKind.ADD_TYPE,
            targets=targets,
            types=CardType.NONE,
            of_chosen_type=True,
            duration=_duration(stream),
            is_targeted=targeted,
            text="is the chosen type in addition to its other types",
        )

    # "every basic land type" - all of them, rather than a named one.
    every = bool(
        stream.accept_phrase("every basic land type")
        or stream.accept_phrase("every creature type")
        or stream.accept_phrase("all creature types")
    )
    spec = None if every else parse_object_filter(stream)
    if spec is None and not every:
        stream.reset(mark)
        return None

    if not (
        stream.accept_phrase("in addition to its other types")
        or stream.accept_phrase("in addition to their other types")
        or stream.accept_phrase("in addition to its other land types")
        or stream.accept_phrase("in addition to their other land types")
        or stream.accept_phrase("in addition to its other creature types")
        or stream.accept_phrase("in addition to their other creature types")
    ):
        stream.reset(mark)
        return None

    duration = _duration(stream)
    return Effect(
        EffectKind.ADD_TYPE,
        targets=targets,
        types=spec.types_all if spec is not None else CardType.NONE,
        keywords=spec.subtypes_all if spec is not None else (),
        duration=duration,
        is_targeted=targeted,
        text="is X in addition to its other types",
    )


@clause("skip-step")
def _skip_step(stream: Stream) -> Effect | None:
    """"Skip your draw step." (CR 500.8.)"""
    from ..rules.kernel.enums import Step

    mark = stream.mark()
    if not stream.accept("skip", "skips"):
        stream.reset(mark)
        return None
    players, _ = parse_player_filter(stream)
    stream.accept("your", "their", "his", "her")

    steps = {
        "untap": Step.UNTAP,
        "upkeep": Step.UPKEEP,
        "draw": Step.DRAW,
        "combat": Step.MAIN,
    }
    word = stream.peek().lower
    if word not in steps:
        stream.reset(mark)
        return None
    stream.next()
    stream.accept("step", "phase")

    return Effect(
        EffectKind.SKIP_STEP,
        players=players or YOU,
        amount=Value.of(int(steps[word])),
        text=f"skip the {word} step",
    )


@clause("double-pt")
def _double_pt(stream: Stream) -> Effect | None:
    """"Double the power and toughness of each creature you control."

    Doubling is a modification by the creature's own current power, which is
    what makes it layer 7c rather than 7b - it stacks with counters rather
    than overwriting them.
    """
    mark = stream.mark()
    if not stream.accept("double"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("the power and toughness of")
        or stream.accept_phrase("the power of")
    ):
        stream.reset(mark)
        return None

    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None
    duration = _duration(stream)

    return Effect(
        EffectKind.MODIFY_PT,
        targets=targets,
        amount=Value(kind=ValueKind.POWER, of_affected=True),
        amount2=Value(kind=ValueKind.TOUGHNESS, of_affected=True),
        duration=duration,
        is_targeted=targeted,
        text="double power and toughness",
    )


@clause("double-counters")
def _double_counters(stream: Stream) -> Effect | None:
    """"Double the number of +1/+1 counters on each creature you control",
    "double the number of each kind of counter on target permanent"
    (CR 701.10e).
    """
    from .nouns import _counter_type

    mark = stream.mark()
    if not stream.accept_phrase("double the number of"):
        return None
    if stream.accept_phrase("each kind of counter"):
        counter = ""
    else:
        counter = _counter_type(stream)
        if not counter:
            stream.reset(mark)
            return None
    if not stream.accept("on"):
        stream.reset(mark)
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.DOUBLE_COUNTERS,
        targets=targets,
        counter_type=counter,
        is_targeted=targeted,
        text="double counters",
    )


@clause("look-at-hand")
def _look_at_hand(stream: Stream) -> Effect | None:
    """"Look at target player's hand." - information, not a card moved."""
    mark = stream.mark()
    if not stream.accept("look"):
        stream.reset(mark)
        return None
    if not stream.accept("at"):
        stream.reset(mark)
        return None

    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("hand", "hands"):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.REVEAL,
        players=players,
        targets=ObjectFilter(zones=frozenset({Zone.HAND})),
        is_targeted=targeted,
        text="look at a hand",
    )


@clause("for-each-do")
def _for_each_do(stream: Stream) -> Effect | None:
    """"For each token you control, create a token that's a copy of it."

    A leading "for each" is a *loop* over matching objects, not a multiplier
    on an amount - the effect happens once per match rather than once at N
    times the size. Adeline and Second Harvest are the shape.
    """
    mark = stream.mark()
    if not stream.accept("for"):
        stream.reset(mark)
        return None

    # "for each opponent" is a *player* phrase whose first word is "each", so
    # the player reader has to see the "each" - consuming it first left it
    # with a bare "opponent", which names nobody.
    look = stream.mark()
    players, _ = parse_player_filter(stream)
    spec = None
    if players is None:
        stream.reset(look)
        if not stream.accept("each", "every"):
            stream.reset(mark)
            return None
        spec = parse_object_filter(stream)
        if spec is None:
            stream.reset(mark)
            return None
    stream.skip_punct(",")

    body = parse_effects(stream)
    if body is None:
        stream.reset(mark)
        return None

    count = (
        Value(kind=ValueKind.COUNT, filter=spec)
        if spec is not None
        else Value(kind=ValueKind.COUNT, players=players)
    )
    return Effect(
        EffectKind.REPEAT,
        amount=count,
        children=tuple(body),
        text="for each ...",
    )


@clause("others-enter-tapped")
def _others_enter_tapped(stream: Stream) -> Effect | None:
    """"Creatures your opponents control enter tapped.", "Lands you control
    enter untapped."

    A replacement effect (CR 614) about somebody else's permanents. The
    self-referring form - a tapland's own "this enters tapped" - was already
    read; this is the same sentence with a filter in front of it instead of
    "this", and it was failing on every stax piece in the format.
    """
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    mark = stream.mark()
    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if not stream.accept("enter", "enters"):
        stream.reset(mark)
        return None

    if stream.accept("tapped"):
        kind = ReplacementKind.ENTERS_TAPPED
        word = "tapped"
    elif stream.accept("untapped"):
        kind = ReplacementKind.ENTERS_UNTAPPED
        word = "untapped"
    else:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.REPLACEMENT,
        replacement_kind=int(kind),
        targets=spec,
        text="those permanents enter " + word,
    )


@clause("poison")
def _poison(stream: Stream) -> Effect | None:
    """"That player gets two poison counters." (CR 122, CR 704.5c.)"""
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("get", "gets"):
        stream.reset(mark)
        return None
    amount = parse_value(stream) or Value.of(1)
    if not (
        stream.accept_phrase("poison counters")
        or stream.accept_phrase("poison counter")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.ADD_POISON,
        players=players,
        amount=amount,
        is_targeted=targeted,
        text="poison counters",
    )


@clause("library-order")
def _library_order(stream: Stream) -> Effect | None:
    """What happens to the cards a look or a reveal set aside.

    "Put one of them into your hand and the rest on the bottom of your
    library in any order", "Put all Goblin cards revealed this way into your
    hand and the rest into your graveyard", "put them back in any order".
    Each part names some of the pile and a place, and a sentence may list
    several parts with the verb said once (Telling Time: "one into your hand,
    one on top of your library, and one on the bottom").

    The parts used to become moves of whatever the resolution *remembered*,
    with no filter and no count: "put a creature card from among them into
    your hand" moved every remembered card, and "the rest" was the same set
    again. Now a part chooses from the pile (``ObjectFilter.from_pile``) by
    its own description and count, and "the rest" is what is left.
    """
    mark = stream.mark()
    if not stream.accept("put", "puts"):
        return None

    # "put them back in any order" - the whole pile, where it already is.
    look = stream.mark()
    if stream.accept("them", "it") or stream.accept_phrase("those cards"):
        if stream.accept("back"):
            _library_words(stream, "on top of")
            return Effect(
                EffectKind.PUT_ON_LIBRARY,
                targets=ObjectFilter(remembered=True),
                keywords=_library_order_words(stream),
                text="put them back",
            )
        stream.reset(look)

    parts = _pile_parts(stream)
    if not parts:
        stream.reset(mark)
        return None
    if len(parts) == 1:
        return parts[0]
    return Effect(EffectKind.SEQUENCE, children=tuple(parts), text="put them")


def _pile_parts(stream: Stream, *, after_first: bool = False) -> list[Effect]:
    """One or more "<some of the pile> <place>" parts, joined by commas and
    "and". The ones after the first may be a bare number ("one on top of your
    library"), which only makes sense once "of them" has been said."""
    parts: list[Effect] = []
    while True:
        look = stream.mark()
        if parts or after_first:
            stream.skip_punct(",")
            stream.accept("and")
        part = _pile_part(stream, continuation=bool(parts) or after_first)
        if part is None:
            stream.reset(look)
            break
        parts.append(part)
    return parts


def _pile_part(stream: Stream, *, continuation: bool) -> Effect | None:
    mark = stream.mark()
    spec = _pile_object(stream, continuation=continuation)
    if spec is None:
        stream.reset(mark)
        return None
    part = _pile_destination(stream, spec)
    if part is None:
        stream.reset(mark)
        return None
    return part


#: "The rest", however a card spells it: everything in the pile not yet put
#: somewhere. "The other" is the same thing when two cards were looked at.
_THE_REST = (
    "all other cards revealed this way",
    "all the other cards revealed this way",
    "all cards revealed this way that weren't put onto the battlefield",
    "the rest of the cards revealed this way",
    "the rest of the revealed cards",
    "the rest of them",
    "the rest",
    "the others",
    "the other",
)


def _pile_object(stream: Stream, *, continuation: bool) -> ObjectFilter | None:
    """Which of the pile a part is about.

    "the rest" is all that is left; "two of them" / "up to one of them" /
    "one of those cards" is a count from the pile; "a creature card from among
    them" and "all land cards revealed this way" are descriptions, read by the
    noun grammar, which marks them as choosing from the pile.
    """
    for phrase in _THE_REST:
        if stream.accept_phrase(phrase):
            return ObjectFilter(from_pile=True)

    mark = stream.mark()
    up_to = stream.accept_phrase("up to")
    number = stream.accept_number()
    if number is None and stream.accept("X"):
        count = Value(kind=ValueKind.X)
    elif number is not None:
        count = Value.of(number)
    else:
        count = None
    if count is not None:
        if stream.accept_phrase("of them") or stream.accept_phrase("of those cards"):
            return ObjectFilter(from_pile=True, count=count, up_to=up_to)
        if continuation and not up_to:
            # "..., one on top of your library, and one on the bottom" - the
            # "of them" was said once for the whole list.
            return ObjectFilter(from_pile=True, count=count)
    stream.reset(mark)

    any_number = stream.at_phrase("any number of")
    spec = parse_object_filter(stream)
    if spec is None or not spec.from_pile:
        stream.reset(mark)
        return None
    if any_number:
        # "any number of" is a choice of how many, which is not "all" - it is
        # kept apart so the round-trip says which. The engine's choice with
        # no agent is every match, which is one legal answer.
        spec = replace(spec, count=None, up_to=True)
    return spec


def _library_order_words(stream: Stream) -> tuple[str, ...]:
    """CR 401.4: "in any order" is the owner's choice (with no agent, the
    order the cards are in), "in a random order" is not a choice at all."""
    if stream.accept_phrase("in any order"):
        return ()
    if stream.accept_phrase("in a random order") or stream.accept_phrase(
        "in random order"
    ):
        return ("random",)
    return ()


def _pile_destination(stream: Stream, spec: ObjectFilter) -> Effect | None:
    """Where a part of the pile goes, as the effect that puts it there."""
    if (
        stream.accept_phrase("into your hand")
        or stream.accept_phrase("into their owner's hand")
        or stream.accept_phrase("into its owner's hand")
        or stream.accept_phrase("into their owners' hands")
    ):
        return Effect(
            EffectKind.MOVE_ZONE, targets=spec, zone=Zone.HAND, text="put into hand"
        )
    if (
        stream.accept_phrase("into your graveyard")
        or stream.accept_phrase("into their owner's graveyard")
        or stream.accept_phrase("into its owner's graveyard")
        or stream.accept_phrase("into their owners' graveyards")
        or stream.accept_phrase("into that player's graveyard")
    ):
        return Effect(
            EffectKind.MOVE_ZONE,
            targets=spec,
            zone=Zone.GRAVEYARD,
            text="put into graveyard",
        )
    if stream.accept_phrase("onto the battlefield"):
        keywords: tuple[str, ...] = ()
        look = stream.mark()
        if stream.accept("tapped"):
            if stream.at("and"):
                # "tapped and attacking" - what it attacks is not said here.
                stream.reset(look)
                return None
            keywords = ("tapped",)
        # CR 110.2a: a permanent is controlled by the player who put it onto
        # the battlefield, which is what the executor does - so "under your
        # control" is that default said aloud.
        stream.accept_phrase("under your control")
        return Effect(
            EffectKind.PUT_ONTO_BATTLEFIELD,
            targets=spec,
            zone=Zone.BATTLEFIELD,
            keywords=keywords,
            text="put onto the battlefield",
        )

    top = None
    if stream.accept("back"):
        top = True
        _library_words(stream, "on top of")
    elif _library_words(stream, "on top of") or _library_words(stream, "on the top of"):
        top = True
    elif stream.accept_phrase("on top"):
        top = True
    elif _library_words(stream, "on the bottom of"):
        top = False
    elif stream.accept_phrase("on the bottom"):
        top = False
    if top is None:
        return None
    order = _library_order_words(stream)
    keywords = order if top else ("bottom", *order)
    return Effect(
        EffectKind.PUT_ON_LIBRARY,
        targets=spec,
        keywords=keywords,
        text="put on top of library" if top else "put on the bottom of library",
    )


def _library_words(stream: Stream, preposition: str) -> bool:
    """"<preposition> your library" and its owner-naming spellings. CR 400.3
    sends a card to its *owner's* library whatever the sentence says, so the
    spellings all mean the same place."""
    for owner in (
        "your library",
        "their library",
        "its owner's library",
        "their owner's library",
        "their owners' libraries",
        "that player's library",
    ):
        if stream.accept_phrase(f"{preposition} {owner}"):
            return True
    return False


@clause("pile-reveal-and-put")
def _pile_reveal_and_put(stream: Stream) -> Effect | None:
    """"Reveal a creature card from among them and put it into your hand."

    CR 701.20: the reveal shows the card; the put moves it. One card, chosen
    once, does both - so the pair is one effect carrying "reveal", not a
    reveal of one choice and a move of whatever the resolution remembered.
    """
    mark = stream.mark()
    if not stream.accept("reveal", "reveals"):
        return None
    spec = _pile_object(stream, continuation=False)
    if spec is None:
        stream.reset(mark)
        return None
    stream.skip_punct(",")
    if not (stream.accept("and") or stream.accept("then")):
        stream.reset(mark)
        return None
    if not stream.accept("put"):
        stream.reset(mark)
        return None
    if not (
        stream.accept("it", "them")
        or stream.accept_phrase("that card")
        or stream.accept_phrase("those cards")
        or stream.accept_phrase("the revealed cards")
        or stream.accept_phrase("the revealed card")
    ):
        stream.reset(mark)
        return None
    moved = _pile_destination(stream, spec)
    if moved is None or moved.kind is EffectKind.PUT_ONTO_BATTLEFIELD:
        stream.reset(mark)
        return None
    moved = replace(moved, keywords=("reveal", *moved.keywords), text="reveal and put")
    # "..., then put that card on top of your library and the rest on the
    # bottom" - the rest of the pile, in the same sentence.
    rest = _pile_parts(stream, after_first=True)
    if rest:
        return Effect(
            EffectKind.SEQUENCE, children=(moved, *rest), text="reveal and put"
        )
    return moved


@clause("pile-exile")
def _pile_exile(stream: Stream) -> Effect | None:
    """"Exile one of them face down", "exile two of those cards" - a count
    from the pile, which the ordinary exile clause's noun grammar cannot read
    ("of them" has no head noun)."""
    mark = stream.mark()
    if not stream.accept("exile"):
        return None
    number = stream.accept_number()
    if number is None or not (
        stream.accept_phrase("of them") or stream.accept_phrase("of those cards")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.EXILE,
        targets=ObjectFilter(from_pile=True, count=Value.of(number)),
        text="exile from the pile",
    )


@clause("exile-zone")
def _exile_zone(stream: Stream) -> Effect | None:
    """"Exile all graveyards." - a zone as the object of a verb.

    Every other effect names cards and says where they are; this one names the
    zone and means everything in it. Read as "every card in every graveyard",
    which is what it does.
    """
    mark = stream.mark()
    if not stream.accept("exile"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("all graveyards") or stream.accept_phrase("each graveyard")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.EXILE,
        targets=ObjectFilter(zones=frozenset({Zone.GRAVEYARD}), count=0),
        zone=Zone.EXILE,
        text="exile every graveyard",
    )


@clause("discard-hand")
def _discard_hand(stream: Stream) -> Effect | None:
    """"Each player discards their hand."

    "Their hand" is a quantity, not a card - however many they happen to be
    holding - so it cannot go through the counted-noun path every other
    discard uses.
    """
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if not stream.accept("discard", "discards"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("their hand")
        or stream.accept_phrase("your hand")
        or stream.accept_phrase("their hands")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.DISCARD,
        players=players or YOU,
        amount=Value(kind=ValueKind.CARDS_IN_HAND, players=players or YOU),
        is_targeted=targeted,
        text="discard your hand",
    )


@clause("top-card-visible")
def _top_card_visible(stream: Stream) -> Effect | None:
    """"Play with the top card of your library revealed.", "You may look at
    the top card of your library any time."

    Two wordings of one permission (CR 715 style continuous effect): the
    controller may see the top card. Bolas's Citadel, Mystic Forge, Vizier of
    the Menagerie and Future Sight all depend on it, and every one of them
    also grants "you may play it", which is a separate sentence and already
    read.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    mark = stream.mark()
    players, _ = parse_player_filter(stream)

    if stream.accept("play"):
        if not stream.accept("with"):
            stream.reset(mark)
            return None
    else:
        stream.accept("may")
        if not stream.accept("look"):
            stream.reset(mark)
            return None
        stream.accept("at")

    if not (
        stream.accept_phrase("the top card of your library")
        or stream.accept_phrase("the top card of their library")
    ):
        stream.reset(mark)
        return None

    # "revealed" and "any time" say the same thing from the two ends: the
    # card is visible. Neither adds a constraint the engine can act on.
    if not (stream.accept("revealed") or stream.accept_phrase("any time")):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.PERMISSION,
        players=players or YOU,
        targets=ObjectFilter(zones=frozenset({Zone.LIBRARY}), from_top=True, count=1),
        duration=Duration.PERMANENT,
        restrictions=(
            Restriction(
                act=Act.LOOK_AT_TOP_CARD,
                players=players or YOU,
                text="may look at the top card of their library",
            ),
        ),
        text="the top card of your library is visible",
    )


@clause("life-total-locked")
def _life_total_locked(stream: Stream) -> Effect | None:
    """"Your life total can't change." (Teferi's Protection, Flare of Fortitude.)

    Not a prohibition on gaining or on losing but on both at once, and on
    every other way a total moves - so it is one act rather than a pair.
    """
    from ..rules.cr500_turn_structure.restrictions import Act, Restriction

    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        if not stream.accept("your"):
            stream.reset(mark)
            return None
        players = YOU
    if not stream.accept_phrase("life total"):
        stream.reset(mark)
        return None
    if not (stream.accept_phrase("can't change") or stream.accept_phrase("cannot change")):
        stream.reset(mark)
        return None

    duration = _duration(stream)
    return Effect(
        EffectKind.RESTRICTION,
        players=players,
        duration=duration,
        restrictions=(
            Restriction(
                act=Act.LIFE_TOTAL_CHANGE,
                players=players,
                text="life total can't change",
            ),
        ),
        text="life total can't change",
    )


@clause("phase-out")
def _phase_out(stream: Stream) -> Effect | None:
    """"Any number of target nonland permanents you control phase out."
    (CR 702.26, CR 702.26b.)

    Phasing is its own zone-like state rather than a move, which is why it
    has an opcode of its own; the engine has had one all along and no
    sentence could reach it.
    """
    mark = stream.mark()
    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("phase out")
        or stream.accept_phrase("phases out")
    ):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.PHASE_OUT,
        targets=targets,
        is_targeted=targeted,
        text="phase out",
    )


@clause("and-the-rest")
def _and_the_rest(stream: Stream) -> Effect | None:
    """"... and the rest on the bottom of your library in any order."

    The verb is elided: "Put one of them into your hand *and the rest on the
    bottom*" says "put" once and means it twice. The put clause reads the
    whole list itself; this is for the rest standing after something else
    took the first half.
    """
    mark = stream.mark()
    stream.skip_punct(",")
    stream.accept("and")
    for phrase in _THE_REST:
        if stream.at_phrase(phrase):
            break
    else:
        stream.reset(mark)
        return None
    part = _pile_part(stream, continuation=False)
    if part is None:
        stream.reset(mark)
        return None
    return part


@clause("becomes-a-copy")
def _becomes_a_copy(stream: Stream) -> Effect | None:
    """"This land becomes a copy of target land, except it has this ability."
    (CR 707.2, applied in layer 1.)

    Distinct from creating a token copy: nothing new enters, an object
    already on the battlefield takes another object's copiable values. The
    engine has had the opcode since the layer system was written.
    """
    mark = stream.mark()
    subject, _ = parse_target(stream)
    if subject is None:
        if not stream.accept("this"):
            stream.reset(mark)
            return None
        stream.accept(*SELF_NOUNS)
        subject = SELF

    if not stream.accept("becomes", "become"):
        stream.reset(mark)
        return None
    if not stream.accept("a", "an"):
        stream.reset(mark)
        return None
    if not stream.accept("copy"):
        stream.reset(mark)
        return None
    if not stream.accept("of"):
        stream.reset(mark)
        return None

    original, targeted = parse_target(stream)
    if original is None:
        stream.reset(mark)
        return None

    _copy_exceptions(stream)
    duration = _duration(stream)

    # The copier is the ability's own source, which is what the executor
    # uses; ``targets`` names the object whose copiable values are taken.
    # That is the convention the other copy clauses already follow.
    # The subject has to be the ability's own source, because that is the
    # only copier the executor knows how to be. "Target creature becomes a
    # copy of ..." is a different card and is left unread rather than
    # silently made to copy the wrong thing.
    if not subject.source_only:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.COPY_PERMANENT,
        targets=original,
        players=YOU,
        duration=duration,
        is_targeted=targeted,
        text="becomes a copy",
    )


def _copy_exceptions(stream: Stream) -> None:
    """The ", except ..." tail on a copy.

    "Except it has this ability", "except the token isn't legendary", "except
    it has haste". Every one of them adjusts the copy rather than choosing a
    different original, and the layer system applies the copy effect before
    anything the exception could change - so consuming the tail loses the
    modification but never the copy, which is the larger half by far.
    """
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("except"):
        stream.reset(mark)
        return
    while not stream.done and stream.peek().text not in (".", ";"):
        if stream.at("until"):
            return
        stream.next()


@clause("extra-trigger")
def _extra_trigger(stream: Stream) -> Effect | None:
    """"If a triggered ability of a Shaman you control triggers, that ability
    triggers an additional time." (CR 603.2b.)

    And the other half of the family, which names the *cause* instead: "if a
    land entering causes a triggered ability of a permanent you control to
    trigger, that ability triggers an additional time." Same effect, and the
    two clauses differ only in which filter is which.
    """
    mark = stream.mark()
    if not stream.accept("if"):
        stream.reset(mark)
        return None

    cause = None
    whose = None

    if stream.accept_phrase("a triggered ability of"):
        whose = parse_object_filter(stream)
        if whose is None:
            stream.reset(mark)
            return None
        if not stream.accept("triggers", "trigger"):
            stream.reset(mark)
            return None
    else:
        # "if a land entering causes a triggered ability of a permanent you
        # control to trigger" - the cause comes first and the affected
        # permanents second.
        cause = parse_object_filter(stream)
        if cause is None:
            stream.reset(mark)
            return None
        # "entering", "being cast", "you casting or copying an instant" - the
        # participle names the event, which the collector does not need: the
        # cause filter and the affected filter together are what decide.
        while not stream.done and not stream.at("causes"):
            if stream.peek().text in (".", ";", ","):
                stream.reset(mark)
                return None
            stream.next()
        if not stream.accept("causes"):
            stream.reset(mark)
            return None
        if not stream.accept_phrase("a triggered ability of"):
            stream.reset(mark)
            return None
        whose = parse_object_filter(stream)
        if whose is None:
            stream.reset(mark)
            return None
        if not stream.accept_phrase("to trigger"):
            stream.reset(mark)
            return None

    stream.skip_punct(",")
    # "that ability triggers an additional time" and "it triggers an
    # additional time" are the same sentence with a shorter pronoun.
    if not (
        stream.accept_phrase("that ability triggers an additional time")
        or stream.accept_phrase("it triggers an additional time")
    ):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.EXTRA_TRIGGER,
        targets=whose,
        trigger_cause=cause,
        amount=Value.of(1),
        text="that ability triggers an additional time",
    )


@clause("player-gains-keyword")
def _player_gains_keyword(stream: Stream) -> Effect | None:
    """"You gain protection from everything until your next turn."

    A player with a keyword, which is rare but real - protection and
    hexproof are both granted to players by the protection package. The
    grant clause reads objects only, so these failed on their own subject.
    """
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("gain", "gains", "have", "has"):
        stream.reset(mark)
        return None

    granted = _quoted_run(stream)
    if not granted:
        stream.reset(mark)
        return None
    duration = _duration(stream)

    return Effect(
        EffectKind.GRANT_ABILITY,
        players=players,
        granted_abilities=granted,
        duration=duration,
        is_targeted=targeted,
        text="a player gains " + ", ".join(a.keyword or a.text for a in granted),
    )


@clause("you-and-permanents")
def _you_and_permanents(stream: Stream) -> Effect | None:
    """"You and permanents you control gain hexproof until end of turn."

    A subject that is a player and a set of objects at once, which no single
    filter can be. Read as the two effects it is: the player gains the
    ability, and so do the permanents. Teferi's Protection and the whole
    protection-package family are written this way.
    """
    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("and"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if not stream.accept("gain", "gains", "have", "has"):
        stream.reset(mark)
        return None

    granted = _quoted_run(stream)
    if not granted:
        stream.reset(mark)
        return None
    duration = _duration(stream)

    names = ", ".join(a.keyword or a.text for a in granted)
    return Effect(
        EffectKind.SEQUENCE,
        children=(
            Effect(
                EffectKind.GRANT_ABILITY,
                players=players,
                granted_abilities=granted,
                duration=duration,
                text=f"you gain {names}",
            ),
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=spec,
                granted_abilities=granted,
                duration=duration,
                text=f"those permanents gain {names}",
            ),
        ),
        text=f"you and your permanents gain {names}",
    )


@clause("its-type-now")
def _its_type_now(stream: Stream) -> Effect | None:
    """"It's an enchantment." - a sentence that changes a type (CR 205.1b).

    The subject is the pronoun for whatever the sentence before it acted on,
    which the resolution remembers. The Enduring cycle is the shape: the
    creature comes back, and it comes back as an enchantment.
    """
    mark = stream.mark()
    if not (
        stream.accept_phrase("it's")
        or stream.accept_phrase("it is")
        or stream.accept_phrase("they're")
    ):
        stream.reset(mark)
        return None
    stream.accept("still")
    stream.accept("a", "an")

    spec = parse_object_filter(stream)
    if spec is None or not spec.types_all:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.ADD_TYPE,
        targets=ObjectFilter(remembered=True),
        types=spec.types_all,
        keywords=spec.subtypes_all,
        duration=Duration.PERMANENT,
        text="it's an ... now",
    )


@clause("shuffle-into-library")
def _shuffle_into_library(stream: Stream) -> Effect | None:
    """"The owner of target permanent shuffles it into their library."

    A move to the library dressed as a shuffle, and the only sentence in the
    grammar whose subject is a possessive player and whose object is the
    permanent that possessive was taken from. Chaos Warp is the reason it
    matters: read as a plain shuffle it removes nothing.
    """
    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None

    subject = None
    look = stream.mark()
    if stream.accept("of"):
        subject, targeted = parse_target(stream)
        if subject is None:
            stream.reset(look)
    else:
        targeted = False

    if not stream.accept("shuffles", "shuffle"):
        stream.reset(mark)
        return None
    if subject is None:
        if not stream.accept("it", "them"):
            stream.reset(mark)
            return None
        targeted = False
    else:
        stream.accept("it", "them")

    if not stream.accept("into"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("their library")
        or stream.accept_phrase("its owner's library")
        or stream.accept_phrase("their owner's library")
        or stream.accept_phrase("your library")
    ):
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.MOVE_ZONE,
        targets=subject if subject is not None else ObjectFilter(remembered=True),
        zone=Zone.LIBRARY,
        is_targeted=targeted,
        text="shuffle it into its owner's library",
    )


@clause("attach")
def _attach(stream: Stream) -> Effect | None:
    """"Attach it to target creature you control." (CR 701.3.)

    The Equipment moves to a new permanent without changing zone, so it is
    its own keyword action rather than a move. Every "when this enters,
    attach it" and every Equipment-matters trigger in the format needs it.
    """
    mark = stream.mark()
    stream.accept("you")
    stream.accept("may")
    if not stream.accept("attach", "attaches"):
        stream.reset(mark)
        return None

    # "attach it", "attach that Equipment", "attach this Aura".
    what, _ = parse_target(stream)
    if what is None:
        stream.reset(mark)
        return None

    if not stream.accept("to"):
        stream.reset(mark)
        return None
    recipient, targeted = parse_target(stream)
    if recipient is None:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.ATTACH,
        targets=recipient,
        attached_to=what,
        is_targeted=targeted,
        text="attach it to something",
    )


@clause("move-counters")
def _move_counters(stream: Stream) -> Effect | None:
    """"Move a counter from target permanent you control onto a second target
    permanent." (CR 121.6.)

    Two effects in one sentence - counters leave one object and arrive on
    another - and the engine has an opcode for each half.
    """
    mark = stream.mark()
    stream.accept("you")
    stream.accept("may")
    if not stream.accept("move"):
        stream.reset(mark)
        return None

    # "all", "any number of", "a", "two" - the quantity is read here rather
    # than by the general value reader, because that reader would take the
    # counter noun with it and leave nothing to name the kind.
    unlimited = bool(
        stream.accept("all") or stream.accept_phrase("any number of")
    )
    number = None if unlimited else stream.accept_number()

    counter = _counter_word(stream)
    if counter is None:
        if not stream.accept("counter", "counters"):
            stream.reset(mark)
            return None
        counter = "*"

    amount = (
        Value(kind=ValueKind.COUNTERS, counter_type=counter)
        if unlimited
        else Value.of(number if number is not None else 1)
    )

    if not stream.accept("from"):
        stream.reset(mark)
        return None
    origin, origin_targeted = parse_target(stream)
    if origin is None:
        stream.reset(mark)
        return None

    if not stream.accept("onto", "to", "on"):
        stream.reset(mark)
        return None
    stream.accept("a", "an")
    stream.accept("second")
    recipient, targeted = parse_target(stream)
    if recipient is None:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.SEQUENCE,
        children=(
            Effect(
                EffectKind.REMOVE_COUNTERS,
                targets=origin,
                counter_type=counter,
                amount=amount,
                is_targeted=origin_targeted,
                text="remove counters",
            ),
            Effect(
                EffectKind.ADD_COUNTERS,
                targets=recipient,
                counter_type=counter,
                amount=amount,
                is_targeted=targeted,
                text="put those counters on it",
            ),
        ),
        text="move counters",
    )


@clause("delayed-sentence")
def _delayed_sentence(stream: Stream) -> Effect | None:
    """"At the beginning of the next end step, exile it." (CR 603.7.)

    A delayed trigger written as its own sentence rather than as a tail. Same
    ability, and the trailing form was the only one read - so "Counter target
    spell. At the beginning of your next main phase, add ..." failed on the
    second sentence and lost the first.
    """
    from .triggers import parse_delayed_when

    mark = stream.mark()
    when = parse_delayed_when(stream)
    if when is None:
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    body = parse_effects(stream)
    if not body:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.DELAYED_TRIGGER,
        trigger=when,
        children=tuple(body),
        text="later",
    )


@clause("extra-phase")
def _extra_phase(stream: Stream) -> Effect | None:
    """"After this phase, there is an additional combat phase."

    And "after this main phase, there is an additional combat phase followed
    by an additional main phase" - the extra-combat family, whose whole point
    is the phase and which the grammar could not say at all.
    """
    mark = stream.mark()
    stream.accept("after")
    stream.accept("this")
    stream.accept("main", "combat")
    stream.accept("phase")
    stream.skip_punct(",")

    if not (
        stream.accept_phrase("there is") or stream.accept_phrase("there's")
    ):
        stream.reset(mark)
        return None
    stream.accept("an", "a")
    if not stream.accept("additional"):
        stream.reset(mark)
        return None

    phases = 0
    while True:
        if not stream.accept("combat", "main"):
            break
        stream.accept("phase")
        phases += 1
        look = stream.mark()
        if stream.accept_phrase("followed by"):
            stream.accept("an", "a")
            stream.accept("additional")
            continue
        stream.reset(look)
        break

    if not phases:
        stream.reset(mark)
        return None
    # "after this phase" and "after this main phase" say when; the engine
    # inserts the phase after the current one either way.
    stream.accept_phrase("after this phase")
    stream.accept_phrase("after this main phase")

    return Effect(
        EffectKind.EXTRA_PHASE,
        players=YOU,
        amount=Value.of(phases),
        text="an additional phase",
    )


@clause("who-cant")
def _who_cant(stream: Stream) -> Effect | None:
    """"Each player who can't discards a card."

    The sentence before it asked every player to do something; this one
    charges the ones who could not. The engine records who complied, so the
    subject is "the players the last effect did not resolve for".
    """
    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("who can't") or stream.accept_phrase("who cannot")
    ):
        stream.reset(mark)
        return None
    stream.accept("does", "do")

    body = parse_effects(stream)
    if not body:
        stream.reset(mark)
        return None
    from ..rules.kernel.query import Condition, ConditionKind

    return Effect(
        EffectKind.CONDITIONAL,
        condition=Condition(
            kind=ConditionKind.NOT,
            operands=(
                Condition(
                    kind=ConditionKind.REMEMBERED_MATCHES,
                    text="the last effect resolved for them",
                ),
            ),
            text="who couldn't",
        ),
        players=players,
        children=tuple(body),
        text="each player who can't",
    )


@clause("distribute-counters")
def _distribute_counters(stream: Stream) -> Effect | None:
    """"Distribute two +1/+1 counters among one or two target creatures you
    control." (CR 601.2d.)

    The division is chosen at announcement and every target must get at
    least one, so the *number of targets* is bounded by the number of
    counters. Read as an ordinary "put N counters on each" it would give two
    counters to each of two creatures - twice the card.
    """
    mark = stream.mark()
    if not stream.accept("distribute"):
        stream.reset(mark)
        return None

    amount = parse_value(stream) or Value.of(1)
    counter = _counter_word(stream)
    if counter is None:
        stream.reset(mark)
        return None
    if not stream.accept("among"):
        stream.reset(mark)
        return None

    targets, targeted = parse_target(stream)
    if targets is None:
        stream.reset(mark)
        return None

    return Effect(
        EffectKind.ADD_COUNTERS,
        targets=targets,
        counter_type=counter,
        amount=amount,
        divided=True,
        is_targeted=targeted,
        text=f"distribute {amount} {counter} counters",
    )


@clause("win-the-game")
def _win_the_game(stream: Stream) -> Effect | None:
    """"You win the game." (CR 104.2a.)"""
    mark = stream.mark()
    players, targeted = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("win", "wins"):
        stream.reset(mark)
        return None
    if not stream.accept_phrase("the game"):
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.PLAYER_WINS,
        players=players,
        is_targeted=targeted,
        text="wins the game",
    )


@clause("two-player-subject")
def _two_player_subject(stream: Stream) -> Effect | None:
    """"You and target opponent each draw a card."

    Two player phrases sharing one verb. No single PlayerFilter names both,
    so it is read as the two effects it is - which is also what the rules
    say happens, simultaneously and to each of them.
    """
    mark = stream.mark()
    first, first_targeted = parse_player_filter(stream)
    if first is None:
        stream.reset(mark)
        return None
    if not stream.accept("and"):
        stream.reset(mark)
        return None
    second, second_targeted = parse_player_filter(stream)
    if second is None:
        stream.reset(mark)
        return None
    if not stream.accept("each"):
        stream.reset(mark)
        return None

    body = parse_effects(stream)
    if not body:
        stream.reset(mark)
        return None

    from dataclasses import replace as _replace

    def _for(who, targeted):
        return tuple(
            _replace(effect, players=who, is_targeted=targeted) for effect in body
        )

    return Effect(
        EffectKind.SEQUENCE,
        children=(*_for(first, first_targeted), *_for(second, second_targeted)),
        text="each of two players",
    )


@clause("class-level")
def _class_level(stream: Stream) -> Effect | None:
    """"Level 2" as the whole effect of an activated ability (CR 716.2a).

    A Class's level-up ability is written as a bare "Level N" after the cost.
    The engine already tracks the level and gates the bands on it; only the
    two-word sentence was unreadable.
    """
    mark = stream.mark()
    if not stream.accept("level"):
        stream.reset(mark)
        return None
    level = stream.accept_number()
    if level is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.SET_CLASS_LEVEL,
        targets=SELF,
        amount=Value.of(level),
        text=f"level {level}",
    )


@clause("spend-only")
def _spend_only(stream: Stream) -> Effect | None:
    """"Spend this mana only to cast creature spells." (CR 106.6).

    A rider on mana already in the pool rather than an effect of its own, so
    it produces no opcode - it narrows the ADD_MANA that came before it, which
    ``_attach_mana_restriction`` does once the sentence is complete.
    """
    mark = stream.mark()
    if not stream.accept_phrase("spend this mana only"):
        stream.reset(mark)
        return None
    stream.accept("to")
    stream.accept("cast", "play", "activate")

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    # "...of the chosen type" (Cavern of Souls). The type was chosen as the
    # permanent entered; the engine holds it, so the phrase narrows the
    # restriction without adding to it here.
    stream.accept_phrase("of the chosen type")
    return Effect(
        EffectKind.NOTHING,
        targets=spec,
        text=SPEND_ONLY_MARK + spec.describe(),
    )


#: Marks the effect that carries a "spend this mana only on" rider, so the
#: preceding ADD_MANA can pick it up.
SPEND_ONLY_MARK = "spend only: "


@clause("prevent-damage")
def _prevent_damage(stream: Stream) -> Effect | None:
    """"Prevent all damage that would be dealt to you this turn." (CR 615)."""
    if not stream.accept("prevent"):
        return None
    if stream.accept("all"):
        amount = Value.of(-1)  # -1 means "all", which the executor reads.
    elif stream.accept_phrase("the next"):
        # "Prevent the next 3 damage that would be dealt to any target this
        # turn" - a shield of a fixed size rather than a blanket.
        amount = parse_value(stream)
        if amount is None:
            amount = Value.of(1)
    else:
        amount = parse_value(stream)
        if amount is None:
            return None

    # "combat damage" is narrower than "damage", and Fog effects are written
    # both ways. The word used to be consumed and forgotten, which turned every
    # Fog into a blanket that also stopped a Lightning Bolt. The shield already
    # chooses which events it watches, so the narrowing is carried on the
    # effect and read by ``_do_prevent_damage``.
    combat_only = bool(stream.accept("combat"))
    if not stream.accept("damage"):
        return None
    marker = ("combat",) if combat_only else ()
    if not stream.accept_phrase("that would be dealt"):
        return None
    # "that would be dealt *by* non-Spider creatures" - whose damage, rather
    # than to whom. It can sit before the recipient or after the duration.
    source = _dealt_by(stream)
    # "that would be dealt *to and dealt by* that creature" - the shield runs
    # both ways round one object (CR 615). Read before the recipient because
    # that is where the card writes it. It used to be consumed and dropped,
    # so Maze of Ith stopped the damage dealt *to* the attacker and let the
    # attacker's own damage through.
    both_ways = source is None and stream.accept_phrase("to and dealt by")
    targets = players = None
    targeted = False
    if both_ways or stream.accept("to"):
        if stream.accept_phrase("any target"):
            targets, targeted = replace(ANY_TARGET), True
        else:
            targets, targeted = parse_target(stream)
        if targets is None:
            players, targeted = parse_player_filter(stream)
            if players is None:
                return None
        elif stream.accept_phrase("and each player"):
            # "to each creature and each player" (Kitsune Palliator).
            players = PlayerFilter(PlayerScope.EACH_PLAYER)
        # "dealt to that creature *and dealt by* it" - the trailing form.
        if not both_ways and targets is not None and source is None:
            look = stream.mark()
            if stream.accept_phrase("and dealt by") and stream.accept_phrase(
                "that creature"
            ) or _accepted_pronoun(stream, look):
                both_ways = True
            else:
                stream.reset(look)
        if both_ways and (targets is None or players is not None):
            return None
    duration = _duration(stream)
    if source is None and not both_ways:
        source = _dealt_by(stream)
        if duration is Duration.PERMANENT:
            duration = _duration(stream)
    if targets is None and players is None and source is None and targeted:
        return None
    return Effect(
        EffectKind.PREVENT_DAMAGE,
        targets=targets,
        players=players,
        damage_source=source,
        both_ways=both_ways,
        amount=amount,
        duration=duration,
        is_targeted=targeted,
        keywords=marker,
        text="prevent damage",
    )


def _dealt_by(stream: Stream):
    """"by <sources>" after "damage that would be dealt" - ``None`` if absent.

    The filter is kept as the noun reader gives it: "by creatures" means
    creatures on the battlefield, "by this artifact" the source itself.
    """
    mark = stream.mark()
    if not stream.accept("by"):
        return None
    if stream.at("a", "an") and stream.at_phrase("a source of your choice"):
        stream.reset(mark)
        return None
    spec = parse_object_filter(stream)
    if spec is None or spec.includes_players:
        stream.reset(mark)
        return None
    return replace(spec, count=None, up_to=False)


def _accepted_pronoun(stream: Stream, look: int) -> bool:
    """"and dealt by it" / "and dealt by them" - the recipient again."""
    stream.reset(look)
    return stream.accept_phrase("and dealt by it") or stream.accept_phrase(
        "and dealt by them"
    )


# ---------------------------------------------------------------------------
# "Where X is ..." (CR 107.3)
# ---------------------------------------------------------------------------


@clause("where-x-is")
def _where_x_is(stream: Stream) -> Effect | None:
    """A trailing "where X is ..." clause.

    The definition trails the effect that uses it (CR 107.3b), so this cannot
    fill it in from where it sits - ``parse_effects`` substitutes once the
    whole sentence is read. Discarding the definition instead would leave X at
    whatever the spell announced, which for a non-X spell is zero: the card
    would parse, resolve, and do nothing at all.
    """
    if not stream.accept("where"):
        return None
    if not stream.accept("X", "x"):
        return None
    if not stream.accept("is"):
        return None
    value = parse_value(stream)
    if value is None:
        return None
    return Effect(EffectKind.NOTHING, amount=value, text="where X is ...")


# ---------------------------------------------------------------------------
# Leading durations and timing qualifiers
# ---------------------------------------------------------------------------


@clause("leading-duration")
def _leading_duration(stream: Stream) -> Effect | None:
    """"Until end of turn, target creature gains flying."

    Oracle text puts the duration first about as often as last, and the
    meaning is identical. Rather than teach every clause to look both ways,
    the leading form parses the rest of the sentence and stamps the duration
    onto whatever came back.
    """
    mark = stream.mark()
    duration = _duration(stream)
    if duration is Duration.PERMANENT:
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    inner = parse_effects(stream)
    if inner is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.SEQUENCE,
        children=tuple(_stamp(effect, duration) for effect in inner),
        duration=duration,
        text="for a duration",
    )


def _stamp(effect: Effect, duration: Duration) -> Effect:
    """Give an effect a duration, unless it already chose one of its own."""
    from dataclasses import replace

    if effect.duration is not Duration.PERMANENT:
        return effect
    return replace(effect, duration=duration)


@clause("timing-qualifier")
def _timing_qualifier(stream: Stream) -> Effect | None:
    """"Activate only as a sorcery.", "Activate only if you control a Forest."

    Consumed whole, to the end of its sentence, and deliberately *not*
    interpreted here. What the restriction means is ``compile``'s to decide,
    because it is a property of the ability rather than an effect the ability
    produces - it changes the ability's timing, its once-per-turn limit or its
    activation condition, none of which a clause can reach.

    Reading it in two places is what made "Activate only as a sorcery and only
    if you control a Goblin" fail: this clause recognised the first half, left
    the second, and the ability died on a conjunction. One reader, one answer,
    and ``compile._activation_condition`` fails the ability when that answer
    is "not understood".
    """
    mark = stream.mark()
    if not stream.accept("activate"):
        return None
    stream.accept_phrase("this ability")
    if not stream.accept("only"):
        stream.reset(mark)
        return None
    while not stream.done and stream.peek().text not in (".", ";"):
        stream.next()
    if stream.mark() == mark:
        return None
    return Effect(EffectKind.NOTHING, text="activate only ...")


# "Spend this mana only to cast creature spells" (CR 106.6) is deliberately
# absent. Consuming it without attaching the restriction to the mana would make
# the engine *more* permissive than the card - mana usable on anything - and a
# parser that loosens a restriction is worse than one that fails: the game
# keeps running and quietly allows plays the card forbids. Until the mana
# system carries the restriction through, these abilities stay unread.


# ---------------------------------------------------------------------------
# Permission to play, and additional costs
# ---------------------------------------------------------------------------


@clause("may-play")
def _may_play(stream: Stream) -> Effect | None:
    """"You may play that card." / "You may cast this card from your graveyard."

    CR 601.3: permission to play from an unusual zone. Playing is not free -
    the spell still pays its cost - so this is PLAY_FROM_ZONE rather than the
    free-cast opcode, and confusing the two would make every impulse-draw card
    a Black Lotus.
    """
    mark = stream.mark()
    stream.accept("you")
    if not stream.accept("may"):
        stream.reset(mark)
        return None
    if not stream.accept("play", "cast"):
        stream.reset(mark)
        return None

    targets, targeted = parse_target(stream)
    if targets is None:
        if not stream.accept("it", "them", "that"):
            return None
        stream.accept("card", "cards")
        targets, targeted = SELF, False

    origin = None
    if stream.accept("from"):
        origin = parse_zone(stream)
        if origin is None:
            return None

    free = bool(
        stream.accept_phrase("without paying its mana cost")
        or stream.accept_phrase("without paying their mana costs")
    )
    _duration(stream)

    return Effect(
        EffectKind.CAST_WITHOUT_PAYING if free else EffectKind.PLAY_FROM_ZONE,
        targets=targets,
        from_zone=origin,
        players=YOU,
        is_targeted=targeted,
        text="you may play it",
    )


@clause("additional-cost")
def _additional_cost(stream: Stream) -> Effect | None:
    """"As an additional cost to cast this spell, sacrifice a creature."

    CR 601.2f: an additional cost is paid while casting, not on resolution.
    The compiler lifts it onto the ability; the clause exists so the sentence
    is fully consumed and the ability is not failed for carrying one.
    """
    if not stream.accept("as"):
        return None
    if not (
        stream.accept_phrase("an additional cost to cast this spell")
        or stream.accept_phrase("an additional cost to cast this ability")
        or stream.accept_phrase("an additional cost")
    ):
        return None
    stream.skip_punct(",")

    inner = parse_effects(stream)
    if inner is None:
        return None

    # "sacrifice an artifact *or* discard a card" - an additional cost
    # written as a choice (CR 601.2b). The payer picks one; reading only the
    # first half failed the sentence on the word "or".
    alternatives = [Effect(EffectKind.SEQUENCE, children=tuple(inner), text="cost")]
    while True:
        look = stream.mark()
        if not stream.accept("or"):
            stream.reset(look)
            break
        other = parse_effects(stream)
        if not other:
            stream.reset(look)
            break
        alternatives.append(
            Effect(EffectKind.SEQUENCE, children=tuple(other), text="cost")
        )

    if len(alternatives) == 1:
        return Effect(
            EffectKind.SEQUENCE,
            children=tuple(inner),
            text="as an additional cost",
        )
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=tuple(alternatives),
        players=YOU,
        text="as an additional cost, one of several",
    )


@clause("only-during")
def _only_during(stream: Stream) -> Effect | None:
    """"Only on your turn." / "During your turn, ..." - a timing qualifier.

    Consumed so the sentence completes. When it qualifies an activated
    ability the compiler has already set the timing from the line; when it
    qualifies a continuous effect the condition is the active player, which
    the engine reads from ``IS_YOUR_TURN``.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    stream.accept("only")
    if not stream.accept("during", "on"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("your turn")
        or stream.accept_phrase("each of your turns")
        or stream.accept_phrase("an opponent's turn")
    ):
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    inner = parse_effects(stream)
    if inner is None:
        return Effect(EffectKind.NOTHING, text="only during your turn")
    return Effect(
        EffectKind.CONDITIONAL,
        condition=Condition(kind=ConditionKind.IS_YOUR_TURN, text="during your turn"),
        children=tuple(inner),
        text="during your turn",
    )


@clause("of-their-choice")
def _of_their_choice(stream: Stream) -> Effect | None:
    """A trailing "of their choice" / "of your choice".

    It says *who* picks, which the engine already routes through the affected
    player's agent (CR 616.1). Consumed so the sentence completes.
    """
    mark = stream.mark()
    if not stream.accept("of"):
        return None
    if not (
        stream.accept_phrase("their choice")
        or stream.accept_phrase("your choice")
        or stream.accept_phrase("its controller's choice")
    ):
        stream.reset(mark)
        return None
    return Effect(EffectKind.NOTHING, text="of their choice")


@clause("modal-inline")
def _modal_inline(stream: Stream) -> Effect | None:
    """A "choose one -" that sits inside a sentence rather than starting one.

    The bullets have already been folded into the line by the splitter when
    they are on their own paragraphs; this handles the inline form, where the
    modes are separated by bullets on the same line.
    """
    if not stream.accept("choose"):
        return None
    if not (
        stream.accept("one")
        or stream.accept("two")
        or stream.accept_phrase("one or both")
        or stream.accept_phrase("up to one")
        or stream.accept_phrase("up to two")
    ):
        return None
    stream.skip_punct("-", "--")
    if stream.done:
        return None

    inner = parse_effects(stream)
    if inner is None:
        return None
    return Effect(
        EffectKind.CHOOSE_MODE, children=tuple(inner), text="choose one"
    )


# ---------------------------------------------------------------------------
# Unless, delayed triggers, copies, energy
# ---------------------------------------------------------------------------


@clause("delayed-trigger")
def _delayed_trigger(stream: Stream) -> Effect | None:
    """"At the beginning of the next end step, sacrifice it." (CR 603.7).

    A delayed trigger created by a resolution. It belongs to the effect that
    made it, not to any object, so it still fires when its source has left.
    """
    from ..rules.cr600_spells_and_abilities.abilities import TriggerCondition
    from ..rules.kernel.events import EventKind as Kind

    mark = stream.mark()
    if not stream.accept("at"):
        return None
    if not stream.accept_phrase("the beginning of"):
        stream.reset(mark)
        return None
    stream.accept("the", "your")
    stream.accept("next")

    if stream.accept_phrase("end step"):
        kind = Kind.END_STEP
    elif stream.accept("upkeep"):
        kind = Kind.UPKEEP
    else:
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    inner = parse_effects(stream)
    if inner is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.DELAYED_TRIGGER,
        trigger=TriggerCondition(
            event_kinds=frozenset({kind}), text="at the beginning of the next step"
        ),
        children=tuple(inner),
        text="delayed trigger",
    )


@clause("copy-permanent")
def _copy_permanent(stream: Stream) -> Effect | None:
    """"Create a token that's a copy of target creature." (CR 707.2).

    A copy takes only the copiable values - layer 1 - so counters, damage and
    auras on the original are not copied. The engine's copy machinery already
    knows that; the parser only has to point it at the right object.
    """
    mark = stream.mark()
    if not stream.accept("create", "creates"):
        stream.reset(mark)
        return None
    parse_value(stream)
    if not stream.accept("token", "tokens"):
        stream.reset(mark)
        return None
    if not (stream.accept_phrase("that's a copy of") or stream.accept_phrase(
        "that are copies of"
    )):
        stream.reset(mark)
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        return None
    # ", except the token has flying and it isn't legendary" - the same tail
    # the becomes-a-copy clause reads. Two clauses for one construct, and
    # only one of them knew about it.
    _copy_exceptions(stream)
    return Effect(
        EffectKind.COPY_PERMANENT,
        targets=targets,
        is_targeted=targeted,
        players=YOU,
        text="create a token copy",
    )


@clause("energy")
def _energy(stream: Stream) -> Effect | None:
    """"You get {E}{E}." - energy counters (CR 122.1, 107.16)."""
    players, _ = parse_player_filter(stream)
    if not stream.accept("get", "gets"):
        return None
    count = 0
    while stream.peek().kind is TokenKind.SYMBOL and stream.peek().text.upper() == "{E}":
        stream.next()
        count += 1
    if not count:
        return None
    return Effect(
        EffectKind.ADD_ENERGY,
        players=players or YOU,
        amount=Value.of(count),
        text=f"get {count} energy",
    )


@clause("kicked")
def _kicked(stream: Stream) -> Effect | None:
    """"If this spell was kicked, ..." (CR 702.33).

    Whether the optional additional cost was paid is recorded on the spell, so
    the condition is about the object rather than the board.
    """
    from ..rules.kernel.query import Condition, ConditionKind

    mark = stream.mark()
    if not stream.accept("if"):
        return None
    if not stream.accept("this"):
        stream.reset(mark)
        return None
    stream.accept("spell", "creature", "permanent")
    if not stream.accept("was"):
        stream.reset(mark)
        return None
    if not stream.accept("kicked"):
        stream.reset(mark)
        return None
    stream.skip_punct(",")

    inner = parse_effects(stream)
    if inner is None:
        stream.reset(mark)
        return None
    return Effect(
        EffectKind.CONDITIONAL,
        condition=Condition(
            kind=ConditionKind.WAS_KICKED, text="this spell was kicked"
        ),
        children=tuple(inner),
        text="if this spell was kicked",
    )


@clause("put-into-hand")
def _put_into_hand(stream: Stream) -> Effect | None:
    """"Put it into your hand." - the tail of a search or a reveal."""
    if not stream.accept("put", "puts"):
        return None
    targets, targeted = parse_target(stream)
    if targets is None:
        if not stream.accept("it", "them", "that"):
            return None
        stream.accept("card", "cards")
        targets, targeted = ObjectFilter(remembered=True), False
    if not stream.accept("into"):
        return None
    zone = parse_zone(stream)
    if zone is None:
        return None
    return Effect(
        EffectKind.MOVE_ZONE,
        targets=targets,
        zone=zone,
        is_targeted=targeted,
        text="put into a zone",
    )


# ---------------------------------------------------------------------------
# Keyword actions (CR 701)
# ---------------------------------------------------------------------------


@clause("attraction-action")
def _attraction_action(stream: Stream) -> Effect | None:
    """"Open an Attraction", "open two Attractions", "roll to visit your
    Attractions" (CR 701.51, 701.52).

    Both are keyword actions the registry expands, but neither reads as one
    to the generic clause: "an" and "two" are counts, so the action's name
    is split by its own number, and "roll to visit" is four words ahead of
    the noun it acts on.
    """
    from ..rules.cr700_additional_rules.cr701_keyword_actions import build

    mark = stream.mark()
    stream.accept("you")
    if stream.accept("open", "opens"):
        count = stream.accept_number()
        if count is not None and stream.accept("Attraction", "Attractions"):
            return build(
                "Open an Attraction", amount=count, text="open an Attraction"
            )[0]
    elif stream.accept("roll", "rolls") and stream.accept_phrase("to visit"):
        if stream.accept("your", "their") and stream.accept("Attractions"):
            return build(
                "Roll to Visit Your Attractions", text="roll to visit your Attractions"
            )[0]
    stream.reset(mark)
    return None


@clause("keyword-action")
def _keyword_action(stream: Stream) -> Effect | None:
    """A bare keyword action: "proliferate", "investigate", "populate".

    CR 701 defines these as verbs that expand into effects, and the engine
    already owns that expansion in ``keyword_actions``. The parser's whole job
    is to notice the verb and hand over the parameter.

    Registered late so the hand-written clauses above win where they exist -
    "draw a card" is not a keyword action and "scry 2" has a clause that also
    reads its count. What reaches here is everything else, which until now was
    nothing: fifty-nine working keyword actions were unreachable from oracle
    text because nothing ever called the registry. A card that said
    "proliferate" failed to parse, and every card in the pool whose only
    effect was a keyword action was inert.
    """
    from ..rules.cr700_additional_rules.cr701_keyword_actions import BUILDERS, build

    mark = stream.mark()
    # "it connives", "that creature explores" - the subject comes first, and
    # the engine resolves the pronoun from what the resolution remembers.
    subject, subject_targeted = parse_target(stream)
    # Longest match first: "venture into the dungeon" and "collect evidence"
    # are single actions whose first word is not one. Each candidate length
    # records where it ends, so backing off restores the cursor exactly -
    # the old arithmetic reset relative to the *subject's* start and landed
    # in the wrong place whenever there was a subject.
    words: list[str] = []
    ends: list[int] = []
    for _ in range(4):
        token = stream.peek()
        if token.kind is not TokenKind.WORD:
            break
        words.append(token.text)
        stream.next()
        ends.append(stream.mark())

    matched: str | None = None
    while words:
        matched = _known_action(" ".join(words), BUILDERS)
        if matched is not None:
            stream.reset(ends[len(words) - 1])
            break
        words.pop()

    if matched is None:
        stream.reset(mark)
        return None

    amount = parse_value(stream)
    if amount is not None and not amount.is_constant:
        # The builders take a printed number. A computed one ("amass X",
        # "reveal the number of ...") would be dropped to nothing, which is
        # swallowing the quantity; left unread instead.
        stream.reset(mark)
        return None
    spec, targeted = parse_target(stream)
    if (
        spec is not None
        and spec.up_to
        and spec.count is None
        and matched.lower() not in _ANY_NUMBER_ACTIONS
    ):
        # "Discard any number of cards": the player's chosen count (CR
        # 107.1c) lives on the filter, and only the actions whose executor
        # picks from the filter can honour it. Discard counts by amount and
        # would discard one.
        stream.reset(mark)
        return None
    if spec is None and subject is not None:
        spec, targeted = subject, subject_targeted
    effects = build(
        matched,
        amount=amount.constant if amount is not None and amount.is_constant else 0,
        filter=spec,
        text=" ".join(words),
    )
    if not effects:
        stream.reset(mark)
        return None
    if any(node.is_unparsed for effect in effects for node in effect.walk()):
        # The registry knows the name but not the behaviour. Failing here is
        # the honest outcome: an ability that half-happens is worse than one
        # that visibly did not parse.
        stream.reset(mark)
        return None

    if len(effects) == 1:
        return effects[0] if not targeted else _retarget(effects[0], targeted)
    return Effect(
        EffectKind.SEQUENCE, children=tuple(effects), text=" ".join(words)
    )


#: Keyword actions whose executor acts on the objects its filter picks, and so
#: can carry "any number of" as the player's choice (``resolve._objects``).
_ANY_NUMBER_ACTIONS = frozenset(
    {"destroy", "exile", "sacrifice", "tap", "untap", "reveal"}
)


def _known_action(phrase: str, builders) -> str | None:
    """The registry name for a verb as a card conjugates it.

    Oracle text writes keyword actions in whatever person the sentence needs:
    "proliferate", "it connives", "that player mills". The registry is keyed
    on the bare verb, so every third-person form missed - a card saying
    "connives" parsed as nothing at all.
    """
    lowered = phrase.lower()
    if lowered in builders:
        return lowered
    for stripped in _deconjugate(lowered):
        if stripped in builders:
            return stripped
    return None


def _deconjugate(phrase: str) -> list[str]:
    """Candidate base forms of a conjugated verb, commonest first."""
    head, _, tail = phrase.partition(" ")
    forms = []
    if head.endswith("ies"):
        forms.append(head[:-3] + "y")
    if head.endswith("es"):
        forms.append(head[:-2])
        forms.append(head[:-1])
    if head.endswith("s"):
        forms.append(head[:-1])
    if head.endswith("ed"):
        forms.append(head[:-2])
    return [f"{form} {tail}".strip() for form in forms]


def _retarget(effect: Effect, targeted: bool) -> Effect:
    from dataclasses import replace

    return replace(effect, is_targeted=targeted)


@clause("amount-replacement")
def _amount_replacement(stream: Stream) -> Effect | None:
    """CR 614, the amount-changing family, read as one grammar.

    Every one of these is the same sentence::

        If <event> would <happen>, <the same event, resized> instead.

    "twice that many counters", "double that damage", "twice that much life",
    "that many plus one" - the substitute is not a *new* effect, it is the
    original event with a different number. Read as a new effect they would
    each produce a second batch rather than a bigger one: a card that still
    runs and does the wrong thing.

    So the event half chooses the ReplacementKind and says exactly which
    events it catches - whose damage, dealt to whom, which kind of counter,
    which tokens, whose life - and the substitute half supplies a multiplier
    and an addend. Every word of the substitute is either the number or a
    restatement of the event ("to that permanent or player", "of those
    tokens"); anything else declines. The first version of this clause
    skipped up to 24 words before "instead", and with them "plus 1", "minus
    1", "draw ... cards" and "that many 4/4 Angel tokens" - so Lich gained
    life instead of drawing and every "+1" damage and life card was a no-op.
    """
    mark = stream.mark()
    if not stream.accept("if"):
        return None

    event = _replaced_event(stream)
    if event is None or not stream.accept(","):
        stream.reset(mark)
        return None

    resized = _resized_event(stream, event)
    if resized is None or not stream.accept("instead"):
        stream.reset(mark)
        return None
    kind, multiplier, extra = resized

    return Effect(
        EffectKind.REPLACEMENT,
        targets=event.subject,
        players=event.players,
        counter_type=event.counter,
        amount=Value.of(extra),
        multiplier=multiplier,
        replacement_kind=int(kind),
        damage_source=event.damage_source,
        actor=event.actor,
        keywords=event.keywords,
        condition=event.condition,
        duration=Duration.PERMANENT,
        text="replacement: change the amount",
    )


class _ReplacedEvent:
    """What the "if ... would ..." half of an amount replacement catches."""

    __slots__ = (
        "kind", "family", "subject", "players", "counter", "actor",
        "damage_source", "keywords", "condition", "recipient_words",
    )

    def __init__(self, kind, family, **fields) -> None:
        from ..rules.kernel.query import ALWAYS

        self.kind = kind
        self.family = family
        self.subject = fields.get("subject")
        self.players = fields.get("players")
        self.counter = fields.get("counter", "")
        self.actor = fields.get("actor")
        self.damage_source = fields.get("damage_source")
        self.keywords = fields.get("keywords", ())
        self.condition = fields.get("condition", ALWAYS)
        self.recipient_words = fields.get("recipient_words", ())


def _replaced_event(stream: Stream):
    """Which event a replacement catches, or ``None``.

    Four families, each with its own reader because each names different
    participants: counters (a kind and a recipient), tokens (a kind of token
    and whose control), damage (a source and a recipient) and life (a player).
    Draws are not here: the engine does not replace draws event by event.
    """
    for reader in (_counter_event, _token_event, _damage_event, _life_event):
        mark = stream.mark()
        event = reader(stream)
        if event is not None:
            return event
        stream.reset(mark)
    return None


def _counter_event(stream: Stream):
    """"one or more +1/+1 counters would be put on a creature you control",
    "an effect / you / an opponent would put one or more counters on ..."."""
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    actor = None
    keywords: tuple[str, ...] = ()
    if stream.accept_phrase("one or more"):
        counter = _any_counter(stream)
        if counter is None:
            return None
        if not (
            stream.accept_phrase("would be put on")
            or stream.accept_phrase("would be placed on")
        ):
            return None
    else:
        if stream.accept_phrase("an effect"):
            # CR 118: counters put as a *cost* are not put by an effect, and
            # neither are the turn-based lore counters of CR 714.3b.
            keywords = ("an effect",)
        elif stream.accept("you"):
            actor = YOU
        elif stream.accept_phrase("an opponent"):
            actor = PlayerFilter(PlayerScope.OPPONENT)
        else:
            return None
        if not stream.accept_phrase("would put one or more"):
            return None
        counter = _any_counter(stream)
        if counter is None or not stream.accept("on"):
            return None

    start = stream.mark()
    spec = parse_object_filter(stream)
    if spec is None:
        return None
    if stream.at("or"):
        # "on a permanent or player", "or on yourself": the counters a player
        # gets (poison, energy, experience) do not pass through the
        # replacement layer, so a card that multiplies them cannot be
        # represented and is left unread rather than half-applied.
        return None
    return _ReplacedEvent(
        ReplacementKind.MODIFY_COUNTERS,
        "counters",
        subject=replace(spec, count=None, up_to=False),
        counter=counter,
        actor=actor,
        keywords=keywords,
        recipient_words=_words_since(stream, start),
    )


def _token_event(stream: Stream):
    """"one or more [creature] tokens would be created [under your control]",
    "an effect would create one or more tokens", "you would create ..."."""
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    players = None
    passive = stream.accept_phrase("one or more")
    if not passive:
        # CR 111.1: tokens are only ever created by effects, so "an effect
        # would create" says nothing "would be created" does not.
        if stream.accept("you"):
            players = YOU
        elif not stream.accept_phrase("an effect"):
            return None
        if not stream.accept_phrase("would create one or more"):
            return None

    spec = parse_object_filter(stream)
    took_token = spec is not None and spec.is_token
    if not (stream.accept("token", "tokens") or took_token):
        return None
    wanted = _token_kind(spec)
    if wanted is False:
        return None

    if passive and not stream.accept_phrase("would be created"):
        return None
    if stream.accept_phrase("under your control"):
        players = YOU
    return _ReplacedEvent(
        ReplacementKind.MODIFY_TOKENS, "tokens", subject=wanted, players=players
    )


def _token_kind(spec):
    """The kind of token a token replacement names - ``None`` for any token,
    or ``False`` when the description says something a token definition that
    does not exist yet cannot answer ("tokens you control with flying")."""
    if spec is None:
        return None
    blank = ObjectFilter()
    kind = ObjectFilter(
        types_any=spec.types_any,
        types_all=spec.types_all,
        types_none=spec.types_none,
        subtypes_any=spec.subtypes_any,
        subtypes_all=spec.subtypes_all,
        subtypes_none=spec.subtypes_none,
        colors_any=spec.colors_any,
        colors_all=spec.colors_all,
        colors_none=spec.colors_none,
    )
    rest = replace(
        spec,
        types_any=blank.types_any,
        types_all=blank.types_all,
        types_none=blank.types_none,
        subtypes_any=(),
        subtypes_all=(),
        subtypes_none=(),
        colors_any=blank.colors_any,
        colors_all=blank.colors_all,
        colors_none=blank.colors_none,
        is_token=None,
        count=None,
        up_to=False,
        zones=blank.zones,
    )
    if rest != blank:
        return False
    return None if kind == blank else kind


def _damage_event(stream: Stream):
    """"a source you control would deal [combat|noncombat] damage [to ...]"."""
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    start = stream.mark()
    source = parse_object_filter(stream)
    if source is None:
        return None
    named = {w.lower() for w in _words_since(stream, start)}
    if not stream.accept_phrase("would deal"):
        return None
    keywords: tuple[str, ...] = ()
    if stream.accept("combat"):
        keywords = ("combat",)
    elif stream.accept("noncombat"):
        keywords = ("noncombat",)
    if not stream.accept("damage"):
        return None

    source = replace(source, count=None, up_to=False)
    if named & {"source", "sources"}:
        # CR 609.7: a source of damage is an object wherever it is - a spell
        # on the stack as much as a permanent - so the noun "source" carries
        # no zone. Left at the battlefield, "a source you control" never
        # caught a Lightning Bolt.
        source = replace(source, zones=frozenset())
    if source == ObjectFilter(zones=frozenset()):
        source = None  # "a source": any damage at all.

    subject = players = None
    words: tuple[str, ...] = ()
    if stream.accept("to"):
        recipient = _damage_recipient(stream)
        if recipient is None:
            return None
        subject, players, words = recipient
    return _ReplacedEvent(
        ReplacementKind.MODIFY_DAMAGE,
        "damage",
        subject=subject,
        players=players,
        damage_source=source,
        keywords=keywords,
        recipient_words=words,
    )


#: The players a damage or prevention recipient can name, as the words say
#: them. "an opponent" is any one of them: the replacement asks whether the
#: player being dealt damage is an opponent.
_RECIPIENT_PLAYERS = (
    ("you", PlayerScope.YOU),
    ("an opponent", PlayerScope.OPPONENT),
    ("a player", PlayerScope.EACH_PLAYER),
    ("player", PlayerScope.EACH_PLAYER),
)


def _damage_recipient(stream: Stream):
    """Who is dealt the damage: an object, a player, or "X or Y" of the two.

    Returns ``(object filter, player filter, the words read)``. More than one
    object description or more than one kind of player declines - the shield
    and the replacement hold one of each.
    """
    start = stream.mark()
    subject = players = None
    while True:
        for phrase, scope in _RECIPIENT_PLAYERS:
            if stream.accept_phrase(phrase):
                if players is not None:
                    return None
                players = PlayerFilter(scope)
                break
        else:
            spec = parse_object_filter(stream)
            if spec is None or subject is not None:
                return None
            if spec.includes_players:
                # "a permanent or player" - the noun reader folds the player
                # into the filter as it does for "any target".
                if players is not None:
                    return None
                players = PlayerFilter(PlayerScope.EACH_PLAYER)
                spec = replace(spec, includes_players=False)
            subject = replace(spec, count=None, up_to=False)
        if not stream.accept("or"):
            break
    return subject, players, _words_since(stream, start)


def _life_event(stream: Stream):
    """"you would gain life", "an opponent would lose life during your turn"."""
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind
    from ..rules.kernel.query import Condition, ConditionKind

    players = None
    for phrase, scope in (
        ("you", PlayerScope.YOU),
        ("an opponent", PlayerScope.OPPONENT),
        ("a player", PlayerScope.EACH_PLAYER),
    ):
        if stream.accept_phrase(phrase):
            players = PlayerFilter(scope)
            break
    if players is None or not stream.accept("would"):
        return None
    if stream.accept("gain"):
        kind, family = ReplacementKind.MODIFY_LIFE_CHANGE, "gain"
    elif stream.accept("lose"):
        kind, family = ReplacementKind.MODIFY_LIFE_LOSS, "loss"
    else:
        return None
    if not stream.accept("life"):
        return None
    condition = None
    if stream.accept_phrase("during your turn"):
        condition = Condition(kind=ConditionKind.IS_YOUR_TURN, text="during your turn")
    event = _ReplacedEvent(kind, family, players=players)
    if condition is not None:
        event.condition = condition
    return event


def _words_since(stream: Stream, start: int) -> tuple[str, ...]:
    return tuple(token.lower for token in stream.tokens[start : stream.mark()])


#: What each family's substitute may say it does, and who may say it.
_RESIZE_VERBS = {
    "counters": ("put", "puts"),
    "tokens": ("create", "creates"),
    "damage": ("deal", "deals"),
    "gain": ("gain", "gains"),
    "loss": ("lose", "loses"),
}
_RESIZE_SUBJECTS = {
    "counters": ("it", "they", "you"),
    "tokens": ("it", "they", "you"),
    "damage": ("it", "that source"),
}

#: Pronouns that point back at the event's own recipient. Each is only a
#: restatement when the event named that kind of recipient.
_ANAPHORA = {
    "that permanent or player": ("object", "player"),
    "that player or permanent": ("object", "player"),
    "that player": ("player",),
    "that permanent": ("object",),
    "that creature": ("object",),
    "it": ("object",),
    "them": ("object",),
}


def _resized_event(stream: Stream, event):
    """The substitute half: the same event with a different number.

    Returns ``(kind, multiplier, addend)``, or ``None`` when the substitute is
    anything other than the event resized.
    """
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    family = event.family
    kind = event.kind

    # Who does it: a pronoun for the actor or the source, or for a life event
    # the player it happens to.
    if family in ("gain", "loss"):
        scope = event.players.scope
        if scope is PlayerScope.YOU:
            if not stream.accept("you"):
                return None
        elif not (stream.accept("they") or stream.accept_phrase("that player")):
            return None
    else:
        for word in _RESIZE_SUBJECTS.get(family, ()):
            if stream.accept_phrase(word):
                break

    verb = stream.accept(*_RESIZE_VERBS[family])
    if family == "gain" and not verb and stream.accept("lose", "loses"):
        # CR 614.1a: "that player loses that much life instead" - the gain
        # becomes a loss of the same size (Tainted Remedy).
        if not (stream.accept_phrase("that much life")):
            return None
        return ReplacementKind.LIFE_GAIN_BECOMES_LOSS, 1, 0
    if family in ("gain", "loss", "damage") and not verb:
        return None

    multiplier = 1
    if stream.accept("twice", "double"):
        multiplier = 2
    elif stream.accept("triple"):
        multiplier = 3
    else:
        mark = stream.mark()
        count = stream.accept_number()
        if count is not None and stream.accept("times"):
            multiplier = count
        else:
            stream.reset(mark)
    if not stream.accept("that"):
        return None
    stream.accept("many", "much")

    extra = _plus_minus(stream)
    if not _resized_noun(stream, event):
        return None
    if extra is None:
        extra = _plus_minus(stream)
    if extra is None:
        extra = 0

    if not verb:
        passive = {"counters": ("are put", "are placed"), "tokens": ("are created",)}
        if not any(stream.accept_phrase(p) for p in passive.get(family, ())):
            return None
    if not _restated_recipient(stream, event):
        return None
    if multiplier == 1 and extra == 0:
        return None  # "that many" and nothing else changes nothing.
    return kind, multiplier, extra


def _plus_minus(stream: Stream) -> int | None:
    mark = stream.mark()
    sign = 1 if stream.accept("plus") else -1 if stream.accept("minus") else 0
    if not sign:
        return None
    number = stream.accept_number()
    if number is None:
        stream.reset(mark)
        return None
    return sign * number


def _resized_noun(stream: Stream, event) -> bool:
    """The thing counted, restated: "+1/+1 counters", "of those tokens"."""
    family = event.family
    if family == "counters":
        for phrase in (
            "of each of those kinds of counters",
            "of those kinds of counters",
            "of those counters",
        ):
            if stream.accept_phrase(phrase):
                return True
        mark = stream.mark()
        named = _counter_word(stream)
        if named is None:
            stream.reset(mark)
            return True  # "that many plus one are put on it"
        # A named kind has to be the kind the event caught.
        return bool(event.counter) and named == event.counter
    if family == "tokens":
        stream.accept_phrase("of those tokens")
        return True
    if family == "damage":
        stream.accept("damage")
        return True
    stream.accept("life")
    return True


def _restated_recipient(stream: Stream, event) -> bool:
    """"on that permanent", "to that permanent or player", "to you" - where
    the resized event lands, which must be where it was going anyway."""
    family = event.family
    if family == "counters":
        preposition = "on"
    elif family == "damage":
        preposition = "to"
    else:
        return True
    mark = stream.mark()
    if not stream.accept(preposition):
        return True
    has = set()
    if event.subject is not None:
        has.add("object")
    if event.players is not None:
        has.add("player")
    for phrase, needs in _ANAPHORA.items():
        if stream.accept_phrase(phrase):
            if set(needs) <= has:
                return True
            stream.reset(mark)
            return False
    words = event.recipient_words
    if words and stream.accept_phrase(" ".join(words)):
        return True
    stream.reset(mark)
    return False


def _any_counter(stream: Stream) -> str | None:
    """A counter kind, or the bare word "counters" with no kind named.

    "one or more +1/+1 counters" and "one or more counters" are both real
    templating - Hardened Scales names a kind, Doubling Season does not - and
    a reader that insisted on a kind missed every card in the second group.
    Empty string means "any kind", which is what the card means.
    """
    mark = stream.mark()
    named = _counter_word(stream)
    if named:
        return named
    stream.reset(mark)
    if stream.accept("counter", "counters"):
        return ""
    stream.reset(mark)
    return None


@clause("replacement")
def _replacement(stream: Stream) -> Effect | None:
    """CR 614.1: "If [event] would [happen], instead [effect]."

    The whole family, which the grammar had no shape for at all: damage
    prevention and doubling, counter doubling, "if it would die, exile it
    instead". They share one skeleton - an event, the word "instead", and what
    happens in its place.

    The event half is consumed rather than modelled. That is a real limit and
    worth stating: the engine's replacement layer decides *which* events an
    effect applies to, and a half-read event condition would apply it to the
    wrong ones. So this records that a replacement exists and what it
    substitutes, and the event stays the engine's business.
    """
    mark = stream.mark()
    if not stream.accept("if"):
        return None

    # The event clause runs to the comma; "instead" then sits on either side
    # of the substitute. Both orders are common and mean the same thing:
    #
    #   If you would draw a card, instead draw two cards.
    #   If a creature would die, exile it instead.
    #
    # Reading only the first order missed every card written the second way.
    comma = -1
    steps = 0
    while not stream.done and not stream.at("instead"):
        token = stream.peek()
        if token.text in (".", ";"):
            break
        if token.text == "," and comma < 0:
            comma = stream.mark() + 1
        stream.next()
        steps += 1
        if steps > 40:
            stream.reset(mark)
            return None

    if stream.accept("instead"):
        after = stream.mark()
        inner = parse_effects(stream)
        if inner is not None:
            return _replacement_effect(inner)
        # Nothing after "instead": the substitute was the clause before it.
        if comma < 0:
            stream.reset(mark)
            return None
        stream.reset(comma)
        inner = _effects_until(stream, after - 1)
        if inner is not None:
            stream.reset(after)
            return _replacement_effect(inner)

    stream.reset(mark)
    return None


def _replacement_effect(inner) -> Effect:
    """A replacement whose event half was consumed but not identified.

    Marked ``UNPARSED`` on purpose. The engine registers a replacement only
    when it carries a ``ReplacementKind`` (see ``replacement._static``, which
    skips a kind of zero), so this effect has never done anything - but as a
    ``REPLACEMENT`` node it counted as *understood*, and a card whose whole
    text is "if X would happen, do Y instead" was scored as fully read while
    being inert.

    That is the one outcome the coverage report exists to prevent: it looks
    like coverage. The behaviour is unchanged; what changes is that the card
    now says so, and lands in the work queue with the phrase that beat it.
    """
    return Effect(
        EffectKind.UNPARSED,
        children=tuple(inner),
        duration=Duration.PERMANENT,
        text="if ... would ..., ... instead",
    )


def _effects_until(stream: Stream, limit: int):
    """Parse effects, refusing to read past a point.

    Used for the suffix order, where the substitute sits between the comma and
    the word "instead" - so the parse has to stop exactly there rather than
    running on and swallowing the word it is anchored to.
    """
    effects = []
    while stream.mark() < limit:
        stream.skip_punct(",", ".", ";")
        stream.accept("then", "and")
        if stream.mark() >= limit:
            break
        effect = parse_effect(stream)
        if effect is None or stream.mark() > limit:
            return None
        effects.append(effect)
    return effects or None
