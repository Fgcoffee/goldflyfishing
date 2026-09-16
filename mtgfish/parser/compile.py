"""Oracle text to abilities: the top level, and the AbilityProvider the engine uses.

This is where full consumption is enforced. Every path that produces an
``Ability`` goes through ``_finish``, and ``_finish`` returns an unreadable
ability whenever the token stream was not fully consumed. There is deliberately
no way to get a partially-parsed ability out of this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..rules.abilities import BATTLEFIELD_ONLY, Ability, AbilityKind
from ..rules.card_types import chapter_ability
from ..rules.effects import Effect, EffectKind
from ..rules.enums import Timing, Zone
from ..rules.keywords import lookup as keyword_lookup
from .clauses import parse_effects
from .errors import ParseFailure
from .normalize import normalize
from .split import Line, LineKind, split_abilities
from .tokens import Stream
from .triggers import parse_trigger


@dataclass(slots=True)
class ParsedFace:
    """One card face's abilities, with everything that failed."""

    abilities: tuple[Ability, ...] = ()
    failures: list[ParseFailure] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.abilities)

    @property
    def understood(self) -> int:
        """Abilities that will actually do what the card says."""
        return sum(
            1
            for ability in self.abilities
            if not ability.unparsed
            and not any(
                node.is_unparsed
                for effect in ability.effects
                for node in effect.walk()
            )
        )

    @property
    def fully_parsed(self) -> bool:
        """Read completely - which is not the same as "nothing failed".

        A keyword whose builder produces an UNPARSED effect leaves no failure
        behind: the *shape* was built, the tokens were all consumed, and only
        the behaviour is missing. Counting that as fully parsed is how a card
        like Djinn of Fool's Fall - flying, plus a Plot ability that does
        nothing - was scored as understood.
        """
        if not self.abilities or self.failures:
            return False
        return not any(
            node.is_unparsed
            for ability in self.abilities
            for effect in ability.effects
            for node in effect.walk()
        )


@dataclass(slots=True)
class ParsedCard:
    """A whole card, face by face."""

    name: str
    faces: tuple[ParsedFace, ...] = ()

    @property
    def fully_parsed(self) -> bool:
        return all(face.fully_parsed or not face.abilities for face in self.faces)

    @property
    def failures(self) -> list[ParseFailure]:
        return [failure for face in self.faces for failure in face.failures]


def parse_card(card) -> ParsedCard:
    """Parse every face of a card."""
    faces = tuple(parse_face(card, index) for index in range(len(card.faces)))
    return ParsedCard(name=card.name, faces=faces)


def parse_face(card, face_index: int = 0) -> ParsedFace:
    """Parse one face's text box."""
    try:
        face = card.faces[face_index]
    except (AttributeError, IndexError):
        return ParsedFace()

    text = normalize(face.oracle_text or "", card_name=face.name)
    if not text:
        return ParsedFace()

    is_permanent = _is_permanent(face)
    result = ParsedFace()
    abilities: list[Ability] = []

    for line in split_abilities(text, is_permanent=is_permanent):
        ability = _parse_line(line, result, is_permanent=is_permanent)
        abilities.extend(ability)

    result.abilities = tuple(abilities)
    return result


def _is_permanent(face) -> bool:
    from ..rules.enums import CardType

    type_line = getattr(face, "type_line", None)
    if type_line is None:
        return True
    nonpermanent = CardType.INSTANT | CardType.SORCERY
    return not bool(type_line.types & nonpermanent)


def _parse_line(
    line: Line, result: ParsedFace, *, is_permanent: bool
) -> list[Ability]:
    abilities = _parse_line_body(line, result, is_permanent=is_permanent)
    if not line.max_speed:
        return abilities
    return [_gate_on_max_speed(ability) for ability in abilities]


def _gate_on_max_speed(ability: Ability) -> Ability:
    """CR 702.183c: the ability functions only while its controller is at
    maximum speed.

    A triggered ability takes it as an intervening-if (CR 603.4), which is
    checked both when it would trigger and again on resolution; anything else
    takes it as a static condition. Both are the same question asked at the
    time that kind of ability asks questions.
    """
    from dataclasses import replace

    from ..rules.query import Condition, ConditionKind

    if ability.unparsed:
        return ability
    gate = Condition(kind=ConditionKind.AT_MAX_SPEED, text="at max speed")

    if ability.kind is AbilityKind.TRIGGERED and ability.trigger is not None:
        return replace(
            ability, trigger=replace(ability.trigger, intervening_if=gate)
        )
    return replace(ability, static_condition=gate)


def _parse_line_body(
    line: Line, result: ParsedFace, *, is_permanent: bool
) -> list[Ability]:
    if line.kind is LineKind.KEYWORD:
        return _keyword(line, result)
    if line.kind is LineKind.TRIGGERED:
        return _triggered(line, result)
    if line.kind is LineKind.ACTIVATED:
        return _activated(line, result)
    if line.kind is LineKind.MODAL:
        return _modal(line, result)
    return _plain(line, result, is_permanent=is_permanent)


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


def _keyword(line: Line, result: ParsedFace) -> list[Ability]:
    """A keyword ability, expanded by the registry the engine already owns.

    The parser does not implement keywords - it recognises them and hands the
    name and parameter to ``keyword_impl``. That is what keeps one definition
    of "flying" in the codebase instead of two that can drift apart.
    """
    from ..rules.keyword_impl import build

    name, argument = _split_keyword(line.text)
    if name is None:
        result.failures.append(
            ParseFailure(line.text, "unknown keyword", rule="keyword")
        )
        return [Ability.unreadable(line.text)]

    instance = _keyword_instance(name, argument, line.text)
    return list(build(instance))


def _split_keyword(text: str) -> tuple[str | None, str]:
    """The longest leading prefix that names a keyword, and the rest."""
    from ..rules.keywords import is_known

    words = text.split()
    for length in range(len(words), 0, -1):
        candidate = " ".join(words[:length])
        if is_known(candidate) and keyword_lookup(candidate) is not None:
            return candidate, " ".join(words[length:])
    return None, text


def _keyword_instance(name: str, argument: str, text: str):
    """Turn "Ward {2}" or "Annihilator 2" into a parameterised instance."""
    from ..rules.keyword_impl import KeywordInstance
    from .nouns import parse_object_filter

    amount = 0
    cost = None
    spec = None

    argument = argument.strip().lstrip("-").strip()
    if argument:
        stream = Stream.of(argument)
        number = stream.accept_number()
        if number is not None:
            amount = number
            # "Awaken 4 - {4}{W}", "Reinforce 1 - {1}{R}": a number, then the
            # cost. Only the number was read, so Ondu Rising's awaken cost was
            # empty - and an empty alternative cost is a free spell.
            if stream.accept("-", "--"):
                words = []
                while not stream.done:
                    words.append(stream.next().text)
                if words:
                    cost = _keyword_cost(" ".join(words))
        elif stream.peek().kind.name == "SYMBOL":
            cost = _keyword_cost(argument)
        else:
            # "Cycling - Pay 2 life", "Echo - Discard a card": a cost with no
            # mana in it. Only mana symbols were read, so these costs vanished
            # and Street Wraith's cycling cost nothing but the card.
            from .costs import parse_cost

            parsed, _ = parse_cost(argument)
            if parsed is not None:
                cost = parsed
            else:
                stream.accept("from")
                spec = parse_object_filter(stream)

    return KeywordInstance(
        name, amount=amount, cost=cost, filter=spec, text=text
    )


def _keyword_cost(text: str):
    """A keyword's cost, which is more than its mana symbols as often as not.

    "Escape - {3}{B}{B}, Exile five other cards from your graveyard" was read
    as its symbols and nothing else, so Uro escaped without exiling a card. The
    whole text is a cost now. If it goes on past a comma into something the
    grammar cannot read, the cost fails rather than charging only the part it
    could. Anything else after the symbols is not a cost - "Prototype {1}{B} -
    1/1", "Kicker {B} and/or {R}" - and the symbols stay the cost they were.
    """
    from ..rules.costs import Cost, CostComponent, CostKind
    from ..rules.mana import ManaCost
    from .costs import parse_cost

    stream = Stream.of(text)
    symbols = []
    while stream.peek().kind.name == "SYMBOL":
        symbols.append(stream.next().text)
    mana = Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("".join(symbols))),))
    if symbols and stream.done:
        return mana
    parsed, _ = parse_cost(text)
    if parsed is not None:
        return parsed
    if symbols and stream.peek().text != ",":
        return mana
    return Cost((CostComponent(CostKind.UNPARSED, text=text),))


# ---------------------------------------------------------------------------
# Triggered, activated, static
# ---------------------------------------------------------------------------


def _triggered(line: Line, result: ParsedFace) -> list[Ability]:
    stream = Stream.of(line.text)
    trigger = parse_trigger(stream)
    if trigger is None:
        result.failures.append(
            ParseFailure(
                line.text,
                "unreadable trigger condition",
                remaining=stream.unreached(),
                rule="trigger",
            )
        )
        return [Ability.unreadable(line.text)]

    effects = _modal_effects(line, stream, result) if line.modes else parse_effects(stream)
    if effects is not None and _has_once_each_turn(effects):
        # CR 603.2f: the limit belongs to the trigger, not to the effect that
        # carried the words.
        from dataclasses import replace as _replace

        trigger = _replace(trigger, once_each_turn=True)
    ability = _finish(
        line.text,
        stream,
        effects,
        result,
        rule="triggered",
        build=lambda body: Ability(
            AbilityKind.TRIGGERED,
            effects=tuple(body),
            trigger=trigger,
            chapter=line.chapters[0] if line.chapters else 0,
            text=line.text,
        ),
    )
    if line.chapters and not ability.unparsed:
        # A Saga line can carry several chapter numbers (CR 714.2c), each of
        # which is its own ability with the same effect.
        return [
            chapter_ability(chapter, *ability.effects, text=line.text)
            for chapter in line.chapters
        ]
    return [ability]


def _activated(line: Line, result: ParsedFace) -> list[Ability]:
    from ..rules.costs import CostKind
    from .costs import parse_cost

    cost_text, _, effect_text = line.text.partition(":")
    cost, cost_failure = parse_cost(cost_text)
    if cost is None:
        result.failures.append(
            ParseFailure(line.text, cost_failure or "unreadable cost", rule="cost")
        )
        return [Ability.unreadable(line.text)]

    stream = Stream.of(effect_text)
    # CR 606.3: loyalty abilities are sorcery-speed and once per turn per
    # permanent, regardless of what the ability itself says.
    loyalty = any(c.kind is CostKind.LOYALTY for c in cost.components)
    timing = (
        Timing.SORCERY
        if loyalty or _sorcery_only(effect_text)
        else Timing.INSTANT
    )
    activation = _activation_condition(effect_text)
    if not activation.understood:
        # CR 602.5b: the restriction is part of the ability. An ability read
        # without the sentence limiting it is a different, better ability, so
        # the whole thing fails and the report names the phrase.
        result.failures.append(
            ParseFailure(
                line.text,
                f"unreadable activation restriction: {activation.condition}",
                rule="activation-restriction",
            )
        )
        return [Ability.unreadable(line.text)]

    effects = _modal_effects(line, stream, result) if line.modes else parse_effects(stream)

    def build(body) -> Ability:
        # Which zones the ability works in depends on the effects as well as
        # the cost, so it is known only once the body is read.
        zones = _zones_paid_from(cost, body)
        return Ability(
            AbilityKind.ACTIVATED,
            effects=tuple(body),
            cost=cost,
            timing=timing,
            # CR 605.1a excludes loyalty abilities by name: Chandra's
            # "+1: Add {R}{R}" uses the stack and can be responded to.
            is_mana_ability=not loyalty and _is_mana_ability(body),
            is_loyalty_ability=loyalty,
            once_each_turn=loyalty or activation.once_each_turn,
            functions_in=zones,
            activation_condition=activation.condition,
            text=line.text,
        )

    return [_finish(line.text, stream, effects, result, rule="activated", build=build)]


GRAVEYARD_ONLY = frozenset({Zone.GRAVEYARD})


def _zones_paid_from(cost, effects=()) -> frozenset[Zone]:
    """CR 113.6m: a cost or effect that moves this card out of a zone makes
    the ability function only in that zone.

    "Discard this card" and "Exile this card from your hand" cannot be paid on
    the battlefield (CR 113.6j), so the channel lands and the Spirit Guides are
    activated from hand. Left at the default, Otawara's channel ability was an
    ability of the land in play and never of the card in hand. "Exile this card
    from your graveyard" is the same rule in another zone: Ghoulcaller's
    Accomplice offered it from play, and paid with some other graveyard card.

    An *effect* says it just as plainly: "Return this card from your graveyard
    to your hand" is an ability of the card in the graveyard, and Eternal
    Dragon's was read as an ability of the Dragon on the battlefield - where
    there is no card in a graveyard to return, so it did nothing at all. The
    rule's own exception is an ability that puts the card there first, as
    "Sacrifice this creature: Return it from your graveyard" does; that one
    works where the creature is.
    """
    from ..rules.costs import EXILE_ZONES, CostKind

    put_there_first = False
    for component in cost.components:
        if component.filter is None or not component.filter.source_only:
            continue
        if component.kind is CostKind.DISCARD:
            return frozenset({Zone.HAND})
        zone = EXILE_ZONES.get(component.kind)
        if zone is not None:
            return frozenset({zone})
        if component.kind is CostKind.SACRIFICE:
            put_there_first = True

    for effect in effects:
        for node in effect.walk():
            if _puts_this_card_into_graveyard(node):
                put_there_first = True
            elif not put_there_first and _moves_this_card_out_of_graveyard(node):
                return GRAVEYARD_ONLY
    return BATTLEFIELD_ONLY


def _this_card(spec) -> bool:
    """A filter that means this card. ``None`` is the effect's own source."""
    return spec is None or spec.source_only


def _moves_this_card_out_of_graveyard(node) -> bool:
    """"Return this card from your graveyard to your hand", and its kin."""
    spec = node.targets
    if spec is None or not spec.source_only:
        return False
    return node.from_zone is Zone.GRAVEYARD or spec.zones == GRAVEYARD_ONLY


def _puts_this_card_into_graveyard(node) -> bool:
    if node.kind in (EffectKind.SACRIFICE, EffectKind.DESTROY):
        return _this_card(node.targets)
    return (
        node.kind is EffectKind.MOVE_ZONE
        and node.zone is Zone.GRAVEYARD
        and _this_card(node.targets)
    )


#: "Activate only during your upkeep.", "Activate only as a sorcery and only
#: if you control a Forest." Everything up to the full stop is the restriction.
_ONLY_RE = re.compile(r"activate (?:this ability )?only ([^.]*)", re.IGNORECASE)

#: Restrictions that are the ability's *timing*, read off the line separately.
_TIMING_ONLY = frozenset({"as a sorcery", "any time you could cast a sorcery"})


@dataclass(frozen=True, slots=True)
class _Activation:
    """What an "Activate only ..." sentence said, once read.

    ``understood`` is the load-bearing field. An activation restriction the
    grammar cannot model is not a detail to skip: it is the sentence that says
    how often, and how early, the card may be used.
    """

    condition: object
    once_each_turn: bool = False
    understood: bool = True


def _activation_condition(effect_text: str) -> _Activation:
    """CR 602.5b: "Activate only ..." is part of the ability, not advice.

    Four shapes are modelled. The sorcery-speed one is the ability's timing
    and is read off the line elsewhere; "only during your upkeep" and "only
    during your turn" become conditions checked whenever the ability would be
    offered; "only once each turn" becomes the limit the engine already keeps
    for loyalty abilities; and "only if <condition>" goes through the ordinary
    condition grammar.

    Anything left is reported as not understood, and the caller fails the
    whole ability. Previously an unreadable restriction was *ignored* on the
    battlefield - the ability was offered every time priority came round,
    whatever the card demanded, which makes a once-a-turn engine into an
    arbitrarily repeatable one. That is the direction of error this parser is
    built to refuse: the cost of failing is a card that does nothing and says
    so in the coverage report, and the cost of ignoring is a simulation that
    is quietly faster than the deck.
    """
    from ..rules.enums import Step
    from ..rules.query import (
        ALWAYS,
        Condition,
        ConditionKind,
        NumericConstraint,
        PlayerFilter,
        PlayerScope,
    )

    from .clauses import parse_condition_text

    match = _ONLY_RE.search(effect_text)
    if match is None:
        return _Activation(ALWAYS)

    restriction = match.group(1).strip()
    conditions: list[object] = []
    once_each_turn = False
    understood = True

    for part in re.split(r"\band only\b", restriction, flags=re.IGNORECASE):
        # Compared in lower case, but *parsed* in the case it was printed: the
        # subtype registry knows "Forest" and not "forest", so lowercasing
        # before the condition grammar ran failed every "only if you control
        # a <type>" there is.
        part = part.strip().strip(",").strip()
        lowered = part.lower()
        if not part or lowered in _TIMING_ONLY:
            continue
        if lowered == "during your upkeep":
            conditions.append(
                Condition(
                    kind=ConditionKind.IS_STEP,
                    players=PlayerFilter(PlayerScope.YOU),
                    constraint=NumericConstraint.exactly(int(Step.UPKEEP)),
                    text="only during your upkeep",
                )
            )
        elif lowered in ("during your turn", "on your turn"):
            conditions.append(
                Condition(
                    kind=ConditionKind.IS_YOUR_TURN, text="only during your turn"
                )
            )
        elif lowered == "once each turn":
            once_each_turn = True
        elif lowered.startswith("if "):
            stream = Stream.of(part[3:])
            parsed = parse_condition_text(stream)
            if parsed is None or not stream.done:
                understood = False
            else:
                conditions.append(parsed)
        else:
            understood = False

    if not understood:
        return _Activation(
            Condition(kind=ConditionKind.UNPARSED, text="activate only " + restriction),
            once_each_turn=once_each_turn,
            understood=False,
        )
    if not conditions:
        return _Activation(ALWAYS, once_each_turn=once_each_turn)
    if len(conditions) == 1:
        return _Activation(conditions[0], once_each_turn=once_each_turn)
    return _Activation(
        Condition(
            kind=ConditionKind.AND,
            operands=tuple(conditions),
            text="activate only " + restriction,
        ),
        once_each_turn=once_each_turn,
    )


def _sorcery_only(text: str) -> bool:
    lower = text.lower()
    return "activate only as a sorcery" in lower or "activate this ability only any time you could cast a sorcery" in lower


def _is_mana_ability(effects) -> bool:
    """CR 605.1a: adds mana, does not target, and is not a loyalty ability.

    All three conditions, because an ability that adds mana *and* targets is
    not a mana ability and does use the stack.
    """
    adds_mana = any(
        node.kind is EffectKind.ADD_MANA
        for effect in effects
        for node in effect.walk()
    )
    targets = any(
        node.is_targeted for effect in effects for node in effect.walk()
    )
    return adds_mana and not targets


def _modal_effects(line: Line, stream: Stream, result: ParsedFace):
    """The modes of an ability whose *effect* is modal (CR 700.2).

    Distinct from a wholly modal line: "When this creature enters, choose one
    -" is a triggered ability that happens to choose a mode, so the trigger is
    read normally and this supplies the body.

    Returns ``None`` if any mode is unreadable, which fails the whole ability.
    A modal spell that offers three modes and understands two is not
    two-thirds right - it is a card that can make a choice the engine cannot
    carry out.
    """
    # Consume the header up to the dash rather than listing the words it may
    # contain. "choose one or more" broke a word list that knew "one", "or"
    # and "both" but not "more", and the next set will invent another one.
    while not stream.done:
        if stream.peek().text in ("-", "--"):
            stream.next()
            break
        stream.next()

    children: list[Effect] = []
    for mode in line.modes:
        inner = Stream.of(mode)
        parsed = parse_effects(inner)
        if parsed is None or not inner.done:
            result.failures.append(
                ParseFailure(
                    mode,
                    "unreadable mode",
                    consumed=inner.blocked,
                    remaining=inner.unreached(),
                    rule="modal",
                )
            )
            return None
        children.append(
            Effect(EffectKind.SEQUENCE, children=tuple(parsed), text=mode)
        )

    if not stream.done:
        return None
    return [
        Effect(EffectKind.CHOOSE_MODE, children=tuple(children), text=line.text)
    ]


def _modal(line: Line, result: ParsedFace) -> list[Ability]:
    """CR 700.2: a modal spell, whose modes are chosen on announcement."""
    children: list[Effect] = []
    for mode in line.modes:
        stream = Stream.of(mode)
        effects = parse_effects(stream)
        if effects is None or not stream.done:
            result.failures.append(
                ParseFailure(
                    mode,
                    "unreadable mode",
                    consumed=stream.pos,
                    remaining=stream.remaining(),
                    rule="modal",
                )
            )
            return [Ability.unreadable(line.text)]
        children.append(
            Effect(EffectKind.SEQUENCE, children=tuple(effects), text=mode)
        )

    return [
        Ability.spell(
            Effect(EffectKind.CHOOSE_MODE, children=tuple(children), text=line.text),
            text=line.text,
        )
    ]


def _plain(line: Line, result: ParsedFace, *, is_permanent: bool) -> list[Ability]:
    alternative = _alternative_cost_line(line.text)
    if alternative is not None:
        # CR 118.9: an alternative cost is a property of the *ability*, not an
        # effect it produces, so it cannot come out of a clause. The engine
        # already gathers Ability.alternative_cost and offers the cast; the
        # only missing piece was reading the sentence.
        return [
            Ability(
                AbilityKind.STATIC,
                alternative_cost=alternative,
                functions_in=frozenset(Zone),
                text=line.text,
            )
        ]

    stream = Stream.of(line.text)
    effects = parse_effects(stream)
    kind = AbilityKind.STATIC if is_permanent else AbilityKind.SPELL
    return [
        _finish(
            line.text,
            stream,
            effects,
            result,
            rule="plain",
            build=lambda body: Ability(
                kind,
                effects=tuple(body),
                is_characteristic_defining=_is_cda(body),
                # CR 604.3: a CDA functions in every zone, not just on the
                # battlefield. Tarmogoyf has to be 0/1 in a graveyard.
                functions_in=frozenset(Zone) if _is_cda(body) else BATTLEFIELD_ONLY,
                text=line.text,
            ),
        )
    ]


def _alternative_cost_line(text: str):
    """"You may pay {B} rather than pay this spell's mana cost."

    And its free cousin, "If you control a commander, you may cast this spell
    without paying its mana cost" - the Force cycle and the free-spell cycle,
    which between them are some of the most played cards in the format.

    Returns ``None`` for anything that is not one of these shapes, so an
    ordinary sentence falls through to the clause grammar untouched.
    """
    from ..rules.costs import AlternativeCost, Cost
    from .clauses import parse_condition_text
    from .costs import parse_cost

    stream = Stream.of(text)

    condition = None
    if stream.at("if"):
        stream.next()
        condition = parse_condition_text(stream)
        if condition is None:
            return None
        stream.skip_punct(",")

    if not stream.accept("you"):
        return None
    if not stream.accept("may"):
        return None

    # "you may cast this spell without paying its mana cost"
    look = stream.mark()
    if stream.accept("cast") and (
        stream.accept_phrase("this spell") or stream.accept("this")
    ):
        if stream.accept_phrase("without paying its mana cost"):
            stream.skip_punct(".")
            if stream.done:
                return AlternativeCost(
                    cost=Cost(()),
                    condition=condition,
                    text="cast without paying its mana cost",
                )
        return None
    stream.reset(look)

    # "you may pay {B} ... rather than pay this spell's mana cost"
    words: list[str] = []
    while not stream.done and not stream.at_phrase("rather than pay"):
        if stream.peek().text in (".", ";"):
            return None
        words.append(stream.next().text)
    # The cost is everything before "rather than pay"; anything after it is
    # the condition, read below.
    if stream.done or not words:
        return None

    cost, _reason = parse_cost(" ".join(words))
    if cost is None:
        return None

    stream.accept_phrase("rather than pay")
    if not (
        stream.accept_phrase("this spell's mana cost")
        or stream.accept_phrase("its mana cost")
    ):
        return None
    # "... rather than pay this spell's mana cost *if there are thirteen or
    # more creatures on the battlefield*." Half the cycle puts its condition
    # after the cost rather than before it, and requiring the sentence to end
    # here failed every one of them.
    if stream.accept("if") and condition is None:
        condition = parse_condition_text(stream)
        if condition is None:
            return None

    stream.skip_punct(".")
    if not stream.done:
        return None
    return AlternativeCost(
        cost=cost, condition=condition, text="alternative cost"
    )


def _has_once_each_turn(effects) -> bool:
    from .clauses import ONCE_EACH_TURN_MARK

    return any(
        node.text == ONCE_EACH_TURN_MARK
        for effect in effects
        for node in effect.walk()
    )


def _is_cda(effects) -> bool:
    """Whether the grammar marked this ability characteristic-defining.

    CDA-ness belongs to the ability (CR 604.3) but is discovered while reading
    an effect, so the clause leaves a marker on the effect's text and this is
    the one place that reads it back.
    """
    from .clauses import CDA_MARK

    return any(
        node.text.startswith(CDA_MARK)
        for effect in effects
        for node in effect.walk()
    )


def parse_inner_ability(text: str) -> Ability | None:
    """One ability written inside another card's text.

    Magic nests abilities constantly - "gains 'flying'" is the easy case, but
    "creates a 1/1 Soldier with 'Whenever this creature attacks, draw a card'",
    "gains '{T}: Add {G}'" and "you get an emblem with '...'" all embed a
    complete ability inside an effect. The grammar had no way down: it could
    read a *keyword* being granted and nothing else, which is why the single
    most common place for a parse to give up was the word "Whenever" - in the
    middle of a sentence, inside a quotation.

    Returns ``None`` rather than an unreadable ability, so the caller fails its
    own ability instead of granting something that does nothing. A creature
    that gains an ability the engine cannot run is worse than one that never
    gained it: the text says it happened.
    """
    text = text.strip().strip('"').strip()
    if not text:
        return None

    scratch = ParsedFace()
    lines = split_abilities(text, is_permanent=True)
    if len(lines) != 1:
        # Two abilities behind one pair of quotes. Legal on real cards, but
        # the caller has one slot; better to fail than to grant half.
        return None

    abilities = _parse_line(lines[0], scratch, is_permanent=True)
    if scratch.failures or len(abilities) != 1:
        return None
    ability = abilities[0]
    if ability.unparsed or any(
        node.is_unparsed for effect in ability.effects for node in effect.walk()
    ):
        return None
    return ability


# ---------------------------------------------------------------------------
# The full-consumption gate
# ---------------------------------------------------------------------------


def _finish(
    text: str,
    stream: Stream,
    effects,
    result: ParsedFace,
    *,
    rule: str,
    build,
) -> Ability:
    """The only way an ability leaves this module.

    CR-faithful parsing is not the property being enforced here - the engine
    does that. What is enforced here is that an ability the grammar did not
    *completely* read never becomes a runnable ability. A sentence understood
    up to the word "instead" is not 90% right; it is a different card.
    """
    if effects is None or not stream.done:
        result.failures.append(
            ParseFailure(
                text,
                "not fully consumed",
                consumed=stream.blocked,
                remaining=stream.unreached(),
                rule=rule,
            )
        )
        return Ability.unreadable(text)
    return build(effects)


# ---------------------------------------------------------------------------
# The engine's AbilityProvider
# ---------------------------------------------------------------------------


class OracleAbilities:
    """Supplies abilities to the engine by parsing oracle text.

    Cached by ``(oracle_id, face_index)`` because the engine asks for the same
    card's abilities constantly - once per layer recomputation per object - and
    parsing is far too slow to do on that path.

    ``verdicts`` lets a person overrule the parser. A card someone has marked
    inert is served as unreadable, exactly as if the grammar had failed on it.
    That is the whole value of the review workflow: without this, marking a
    card wrong would change what the UI displays and nothing about what the
    simulation actually does.
    """

    def __init__(self, verdicts=None) -> None:
        self._cache: dict[tuple[str, int], tuple[Ability, ...]] = {}
        self.failures: list[ParseFailure] = []
        self.verdicts = verdicts
        #: Cards served inert because a person said so, for the run report.
        self.suppressed: set[str] = set()

    def abilities_for(self, card, face_index: int) -> tuple[Ability, ...]:
        key = (getattr(card, "oracle_id", "") or getattr(card, "name", ""), face_index)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        name = getattr(card, "name", "")
        if self.verdicts is not None and self.verdicts.is_suppressed(name):
            self.suppressed.add(name)
            self._cache[key] = ()
            return ()

        parsed = parse_face(card, face_index)
        self.failures.extend(parsed.failures)
        self._cache[key] = parsed.abilities
        return parsed.abilities

    def clear(self) -> None:
        self._cache.clear()
        self.failures.clear()
        self.suppressed.clear()


#: Zones a static ability functions in by default (CR 604.3).
DEFAULT_STATIC_ZONES = frozenset({Zone.BATTLEFIELD})
