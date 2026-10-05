"""Oracle text to abilities: the top level, and the AbilityProvider the engine uses.

This is where full consumption is enforced. Every path that produces an
``Ability`` goes through ``_finish``, and ``_finish`` returns an unreadable
ability whenever the token stream was not fully consumed. There is deliberately
no way to get a partially-parsed ability out of this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..rules.cr300_card_types.cr300_card_types import chapter_ability
from ..rules.cr600_spells_and_abilities.abilities import BATTLEFIELD_ONLY, Ability, AbilityKind
from ..rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from ..rules.cr700_additional_rules.keywords import lookup as keyword_lookup
from ..rules.kernel.enums import Timing, Zone
from ..rules.kernel.query import ALWAYS
from .clauses import parse_effects, pile_references_ok
from .errors import ParseFailure
from .normalize import normalize
from .split import Line, LineKind, split_abilities
from .tokens import Stream
from .triggers import parse_trigger


def understood(ability: Ability) -> bool:
    """Whether an ability will actually do what its card says.

    Three ways it can fail, and only the first used to be checked:

    * the ability itself is unreadable;
    * one of its effects is ``UNPARSED`` - a keyword whose builder produced a
      placeholder, say, which leaves no ``ParseFailure`` behind because the
      tokens were all consumed and only the behaviour is missing;
    * a **condition** guarding it is unreadable. An ``UNPARSED`` condition
      never holds, so the ability is inert - safe, and silent. Ward is the
      example that matters: ``Ward {2}`` builds a triggered ability that
      counters the spell "if the cost was not paid", the cost half is not
      read, and the whole keyword parses into something that never fires.
      Counted as understood, a Ward creature looked completely read while
      warding nothing.

    All three are the same question - will the engine do what the card says -
    and the answer belongs in one place, because the coverage report, the deck
    view and the UI all ask it separately.
    """
    if ability.unparsed:
        return False
    for condition in (ability.static_condition, ability.activation_condition):
        if condition is not None and condition.is_unparsed:
            return False
    trigger = ability.trigger
    if trigger is not None:
        gate = getattr(trigger, "intervening_if", None)
        if gate is not None and gate.is_unparsed:
            return False
    for effect in ability.effects:
        for node in effect.walk():
            if node.is_unparsed or node.condition.is_unparsed:
                return False
    return True


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
        return sum(1 for ability in self.abilities if understood(ability))

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
        return all(understood(ability) for ability in self.abilities)


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

    text = normalize(face.oracle_text or "", card_name=face.name, **_self_names(face))
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


def _self_names(face) -> dict:
    """What normalization needs to know to find a face's shortened name.

    CR 201.5c: a legendary card may call itself by a shortened name, and a
    planeswalker's shortened name is also its own planeswalker type - so a
    face's type line decides which forms of its name are self-references.
    """
    from ..rules.kernel.enums import CardType, Supertype

    type_line = getattr(face, "type_line", None)
    if type_line is None:
        return {}
    legendary = bool(type_line.supertypes & Supertype.LEGENDARY)
    walker = bool(type_line.types & CardType.PLANESWALKER)
    return {
        "legendary": legendary,
        "planeswalker_types": tuple(type_line.subtypes) if walker else (),
    }


def _is_permanent(face) -> bool:
    from ..rules.kernel.enums import CardType

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
    """CR 702.178a: the ability functions only while its controller is at
    maximum speed.

    A triggered ability takes it as an intervening-if (CR 603.4), which is
    checked both when it would trigger and again on resolution; anything else
    takes it as a static condition. Both are the same question asked at the
    time that kind of ability asks questions.
    """
    from dataclasses import replace

    from ..rules.kernel.query import Condition, ConditionKind

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
    from ..rules.cr700_additional_rules.cr702_keyword_impl import build

    name, argument = _split_keyword(line.text)
    if name is None:
        result.failures.append(
            ParseFailure(line.text, "unknown keyword", rule="keyword")
        )
        return [Ability.unreadable(line.text)]

    if name.lower() in _BODY_KEYWORDS:
        return [_keyword_with_body(name, argument, line, result)]

    instance = _keyword_instance(name, argument, line.text)
    return list(build(instance))


#: Keywords whose argument is ordinary effect text rather than a number, a
#: cost or a quality. CR 702.159a: "Visit - [Effect]".
_BODY_KEYWORDS = frozenset({"visit"})


def _keyword_with_body(name: str, argument: str, line: Line, result: ParsedFace) -> Ability:
    """A keyword wrapped around effect text: the text after the dash is read
    by the effect grammar and handed to the keyword's builder as its body.

    It goes through ``_finish`` like any other ability, so a body the grammar
    cannot read completely leaves the whole keyword unreadable rather than
    triggering with half an effect.
    """
    from ..rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build

    body_text = argument.strip()
    if body_text.startswith("-"):
        body_text = body_text[1:].strip()
    stream = Stream.of(body_text)
    effects = parse_effects(stream)

    def build_body(body) -> Ability:
        (ability,) = build(KeywordInstance(name, text=line.text, effects=tuple(body)))
        return ability

    return _finish(line.text, stream, effects, result, rule="keyword", build=build_body)


def _split_keyword(text: str) -> tuple[str | None, str]:
    """The longest leading prefix that names a keyword, and the rest."""
    from ..rules.cr700_additional_rules.keywords import is_known

    words = text.split()
    for length in range(len(words), 0, -1):
        candidate = " ".join(words[:length])
        if is_known(candidate) and keyword_lookup(candidate) is not None:
            return candidate, " ".join(words[length:])
    return None, text


def _keyword_instance(name: str, argument: str, text: str):
    """Turn "Ward {2}" or "Annihilator 2" into a parameterised instance."""
    from ..rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance
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
    from ..rules.cr100_game_concepts.cr106_mana import ManaCost
    from ..rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
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


def _chapter(line: Line, result: ParsedFace) -> list[Ability]:
    """CR 714.2b: "{rN} - [Effect]" is a triggered ability whose trigger is
    the chapter symbol itself - the text after the symbol is only the effect,
    so it is read with the effect grammar, never as a trigger condition.

    CR 714.2c: "I, II - [Effect]" is one ability per chapter number, each with
    the same effect.
    """
    stream = Stream.of(line.text)
    effects = _modal_effects(line, stream, result) if line.modes else parse_effects(stream)
    ability = _finish(
        line.text,
        stream,
        effects,
        result,
        rule="chapter",
        build=lambda body: chapter_ability(line.chapters[0], *body, text=line.text),
    )
    if ability.unparsed:
        return [ability]
    if _opens_with_a_back_reference(ability.effects):
        # "III - Return the exiled card to the battlefield": the card is the
        # one chapter I exiled, a link between two abilities (CR 607) that
        # the reading "whatever this ability just acted on" does not make. A
        # chapter ability has no triggering object to fall back on either,
        # so it would do nothing.
        result.failures.append(
            ParseFailure(line.text, "refers back to another chapter", rule="chapter")
        )
        return [Ability.unreadable(line.text)]
    return [
        chapter_ability(chapter, *ability.effects, text=line.text)
        for chapter in line.chapters
    ]


def _opens_with_a_back_reference(effects) -> bool:
    """Whether the first thing an ability acts on is "whatever was just
    referred to" - which, first thing in a resolution, is nothing."""
    for effect in effects:
        for node in effect.walk():
            if node.children or node.otherwise:
                continue
            return node.targets is not None and node.targets.remembered
    return False


def _triggered(line: Line, result: ParsedFace) -> list[Ability]:
    if line.chapters:
        return _chapter(line, result)
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
            text=line.text,
        ),
    )
    return [ability]


def _activated(line: Line, result: ParsedFace) -> list[Ability]:
    from ..rules.cr100_game_concepts.cr118_costs import CostKind
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
    from ..rules.cr100_game_concepts.cr118_costs import EXILE_ZONES, CostKind

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
    from ..rules.kernel.enums import Step
    from ..rules.kernel.query import (
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
    # The header is read, not skipped. Skipping to the dash made every
    # "choose two", "choose one or both" and "choose up to one" a "choose
    # one", and swallowed whatever else stood between the trigger and the
    # dash ("you may pay {1}. When you do, choose one").
    header = _mode_header(stream, len(line.modes))
    if header is not None and line.kind is LineKind.ACTIVATED:
        # CR 602.5b: "Choose one. Activate only once each turn." The
        # restriction was already read off the effect text by
        # ``_activation_condition``, which fails the ability when it cannot.
        _activation_sentence(stream)
    if header is None or not stream.done:
        result.failures.append(
            ParseFailure(
                line.text,
                "unreadable modal header",
                consumed=stream.pos,
                remaining=stream.remaining(),
                rule="modal",
            )
        )
        return None

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

    return [
        Effect(
            EffectKind.CHOOSE_MODE,
            children=tuple(children),
            text=line.text,
            **header,
        )
    ]


def _activation_sentence(stream: Stream) -> None:
    """Consume an "Activate only ..." sentence, which compile reads itself."""
    mark = stream.mark()
    if not stream.accept("activate"):
        return
    stream.accept_phrase("this ability")
    if not stream.accept("only"):
        stream.reset(mark)
        return
    while not stream.done and stream.peek().text != ".":
        stream.next()
    stream.skip_punct(".")


def _mode_header(
    stream: Stream, modes: int, *, is_spell: bool = False
) -> dict | None:
    """How many modes the instruction asks for (CR 700.2, 700.2d).

    Returns the CHOOSE_MODE fields the header sets, or ``None`` for a header
    the engine cannot honour. Every word is accounted for: "choose one that
    hasn't been chosen this turn" is not "choose one", and a header read as
    something it does not say chooses modes the card does not allow.

    - "choose one" / "choose two" / "choose three": exactly that many
    - "choose up to N": at most N, possibly none
    - "choose one or both" / "choose one or more": at least one, at most all
    - "choose any number": at most all, possibly none (Rankle's rulings)
    - then optionally "You may choose the same mode more than once." (700.2d)
    - or an upgrade: "If <condition>, [you may] choose both instead."
    """
    from ..rules.kernel.query import Value

    if not stream.accept("choose"):
        return None
    counted = _mode_count(stream, modes)
    if counted is None:
        return None
    count, fields = counted
    if count != 1 or fields:
        fields["amount"] = Value.of(count)
    if stream.at(".") and stream.peek(1).lower == "you":
        stream.next()
        if not stream.accept_phrase("you may choose the same mode more than once"):
            return None
        fields["modes_may_repeat"] = True
    elif stream.at(".") and stream.peek(1).lower == "if":
        stream.next()
        instead = _mode_upgrade(stream, modes, count, fields, is_spell=is_spell)
        if instead is None:
            return None
        fields["modes_instead"] = instead
    stream.skip_punct(".", "-", "--")
    if not fields.get("modes_may_repeat") and count > modes:
        # More modes demanded than printed, with no licence to repeat one.
        return None
    return fields


def _mode_count(stream: Stream, modes: int) -> tuple[int, dict] | None:
    """The number in a modal header, as a budget and the fields it sets."""
    fields: dict = {}
    if stream.accept_phrase("up to"):
        count = stream.accept_number()
        if count is None or count < 1:
            return None
        fields.update(modes_up_to=True)
    elif stream.accept_phrase("one or both"):
        if modes != 2:
            return None
        count = 2
        fields.update(modes_up_to=True, modes_at_least=1)
    elif stream.accept_phrase("one or more"):
        count = modes
        fields.update(modes_up_to=True, modes_at_least=1)
    elif stream.accept_phrase("any number"):
        count = modes
        fields.update(modes_up_to=True)
    elif stream.accept("both"):
        if modes != 2:
            return None
        count = 2
    else:
        count = stream.accept_number()
        if count is None or count < 1:
            return None
    return count, fields


def _mode_upgrade(
    stream: Stream, modes: int, count: int, fields: dict, *, is_spell: bool
) -> tuple | None:
    """"If <condition>, [you may] choose both instead." (CR 700.2).

    Returns ``(condition, budget, up_to, at_least)`` for the header that
    replaces the printed one when the condition holds as the modes are
    chosen - which for a spell is as it is cast, the moment "as you cast this
    spell" names. "You may choose both instead" keeps the printed count as a
    floor: the player may still choose one.

    Only conditions this grammar reads exactly are accepted. The general
    condition reader turns "you control an artifact and an enchantment"
    into "... or ...", and a mode choice widened on the wrong condition is
    a card doing something it does not allow.
    """
    from ..rules.kernel.query import Condition, ConditionKind
    from .clauses import parse_condition_text

    if not stream.accept("if"):
        return None
    if stream.accept_phrase("this spell was kicked"):
        if not is_spell:
            return None
        condition = Condition(
            kind=ConditionKind.WAS_KICKED, text="this spell was kicked"
        )
    elif stream.at_phrase("you control a commander"):
        condition = parse_condition_text(stream)
        if condition is None:
            return None
        if stream.at_phrase("as you cast this spell"):
            if not is_spell:
                return None
            stream.accept_phrase("as you cast this spell")
    else:
        return None
    stream.skip_punct(",")
    optional = stream.accept_phrase("you may")
    if not stream.accept("choose"):
        return None
    counted = _mode_count(stream, modes)
    if counted is None or not stream.accept("instead"):
        return None
    upgraded, upgraded_fields = counted
    if fields or upgraded > modes or upgraded <= count:
        return None
    if optional:
        if upgraded_fields:
            # "you may choose any number instead" does not say whether the
            # printed one is still owed; nothing prints it.
            return None
        return (condition, upgraded, True, count)
    return (
        condition,
        upgraded,
        upgraded_fields.get("modes_up_to", False),
        upgraded_fields.get("modes_at_least", 0),
    )


def _modal(line: Line, result: ParsedFace) -> list[Ability]:
    """CR 700.2: a modal spell, whose modes are chosen on announcement."""
    stream = Stream.of(line.text)
    header = _mode_header(stream, len(line.modes), is_spell=True)
    if header is None or not stream.done:
        result.failures.append(
            ParseFailure(
                line.text,
                "unreadable modal header",
                consumed=stream.pos,
                remaining=stream.remaining(),
                rule="modal",
            )
        )
        return [Ability.unreadable(line.text)]
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
        _bound(
            line.text,
            Ability.spell(
                Effect(
                    EffectKind.CHOOSE_MODE,
                    children=tuple(children),
                    text=line.text,
                    **header,
                ),
                text=line.text,
            ),
            result,
            rule="modal",
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

    def build_plain(body) -> Ability:
        gate = ALWAYS
        if kind is AbilityKind.STATIC:
            gate, body = _lift_static_condition(body)
        return Ability(
            kind,
            effects=tuple(body),
            static_condition=gate,
            is_characteristic_defining=_is_cda(body),
            # CR 604.3: a CDA functions in every zone, not just on the
            # battlefield. Tarmogoyf has to be 0/1 in a graveyard.
            functions_in=frozenset(Zone) if _is_cda(body) else BATTLEFIELD_ONLY,
            text=line.text,
        )

    return [
        _finish(line.text, stream, effects, result, rule="plain", build=build_plain)
    ]


def _lift_static_condition(body):
    '''Move a whole-ability condition off the effect and onto the ability.

    "Creatures you control get +1/+1 as long as you control a Forest" parses
    to a CONDITIONAL wrapping a MODIFY_PT. That is the right shape for a
    resolving spell and the wrong one for a static ability: the layer system
    gathers continuous effects by walking SEQUENCEs and never looks inside a
    CONDITIONAL, so *both* halves were dropped and the anthem did nothing -
    while parsing cleanly, which is the worst way to be wrong.

    ``Ability.static_condition`` is the mechanism the engine already has for
    exactly this (``layers._static_condition_holds``), and it is re-checked
    every time characteristics are recomputed, which is what a continuously
    checked condition needs. So the condition is lifted and the continuous
    effects come back to the top level where the layer system can see them.

    Only for a condition wrapping continuous effects, and only with no
    "otherwise" branch: a static ability that does one thing or else another
    is not a gate, and a CONDITIONAL around a one-shot effect is not one
    either.
    '''
    from ..rules.cr600_spells_and_abilities.effects import CONTINUOUS_KINDS

    if len(body) != 1:
        return ALWAYS, body
    node = body[0]
    if node.kind is not EffectKind.CONDITIONAL or node.otherwise:
        return ALWAYS, body
    if node.condition.is_always or node.condition.is_unparsed:
        return ALWAYS, body
    if not node.children:
        return ALWAYS, body
    if not all(child.kind in CONTINUOUS_KINDS for child in node.children):
        return ALWAYS, body
    return node.condition, list(node.children)


def _alternative_cost_line(text: str):
    """"You may pay {B} rather than pay this spell's mana cost."

    And its free cousin, "If you control a commander, you may cast this spell
    without paying its mana cost" - the Force cycle and the free-spell cycle,
    which between them are some of the most played cards in the format.

    Returns ``None`` for anything that is not one of these shapes, so an
    ordinary sentence falls through to the clause grammar untouched.
    """
    from ..rules.cr100_game_concepts.cr118_costs import AlternativeCost, Cost
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
    # ``understood`` rather than a walk for UNPARSED nodes: a quoted "Ward -
    # Pay 2 life" builds a trigger whose *condition* is unread, which the walk
    # never looked at, so the grant counted as read while granting nothing.
    if not understood(ability):
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
    if not pile_references_ok(effects):
        # "The rest" with nothing looked at, or on only one branch of an "if
        # you don't": every word was read, and the sentences do not fit
        # together into something the engine would do as printed.
        result.failures.append(
            ParseFailure(text, "pile reference without a pile", rule=rule)
        )
        return Ability.unreadable(text)
    return _bound(text, build(effects), result, rule=rule)


def _share_targets(effects):
    """Mark the verbs that share one word "target" (CR 115.3, 601.2c).

    "Target creature gets +2/+0 and gains first strike" is read as two
    instructions, and the clause readers build both around the *same*
    filter object - one noun phrase, read once. Each targeted instruction
    held a target of its own, so the spell asked for two choices and could
    pump one creature and give first strike to another. The second of such
    a pair is marked ``same_target``: it holds no target and acts on the
    one chosen for the first.

    Only the targeted instruction just before counts, in the order the
    targets are chosen (``targeted_nodes``), because that is the choice the
    resolver has just handed out. A filter shared with an earlier target
    across a different one is not something the engine can line up, and
    ``None`` refuses the ability.
    """
    from dataclasses import replace

    slots: list = []  # the filter of every target slot so far, in order
    refused = False

    def visit(node):
        nonlocal refused
        if node.kind is EffectKind.CHOOSE_MODE and node.children:
            # Each mode's targets follow those before the modal instruction
            # (CR 700.2c); no mode's verbs share another mode's target.
            before = list(slots)
            modes = []
            for mode in node.children:
                slots[:] = before
                modes.append(visit(mode))
            slots[:] = before + [object()]
            return replace(node, children=tuple(modes))
        if node.is_targeted and not node.same_target:
            spec = node.targets
            if spec is not None and not node.targets_its_player and slots and slots[-1] is spec:
                node = replace(node, same_target=True)
            else:
                if spec is not None and any(earlier is spec for earlier in slots):
                    refused = True
                slots.append(spec if spec is not None else object())
        children = tuple(visit(child) for child in node.children)
        otherwise = tuple(visit(child) for child in node.otherwise)
        if any(a is not b for a, b in zip(children + otherwise, node.children + node.otherwise)):
            node = replace(node, children=children, otherwise=otherwise)
        return node

    shared = tuple(visit(effect) for effect in effects)
    return None if refused else shared


def _refers_back(node) -> bool:
    """Whether an instruction names an object by what the resolution
    remembers: "it", "those creatures", "that creature's power"."""

    def value_refers(value) -> bool:
        if value is None:
            return False
        spec = getattr(value, "filter", None)
        if spec is not None and spec.remembered:
            return True
        return any(value_refers(op) for op in getattr(value, "operands", ()))

    return bool(
        (node.targets is not None and node.targets.remembered)
        or (node.damage_source is not None and node.damage_source.remembered)
        or value_refers(node.amount)
        or value_refers(node.amount2)
    )


def _other_than_earlier_targets(effects):
    """"Target creature gets +2/+2. Another target creature gets -2/-2":
    after a target, "other" means other than *that target* (CR 115.3 lets
    one object be chosen for each instance of the word "target" unless the
    card says otherwise). The parser's filter says "other than this
    object's source", which on a spell excludes nothing - the same creature
    could be chosen twice - and on a permanent's ability excludes the wrong
    object. Such a target is rewritten to one that must differ from every
    earlier target of the ability (``Effect.distinct_from_earlier_targets``,
    enforced as targets are chosen, CR 601.2c) and no longer excludes the
    source. With no earlier target ("exile up to one other target
    creature") the word means the source and is left alone.

    Returns the rewritten effects, the effects unchanged, or ``None`` to
    refuse. Refused, as before, where the engine cannot say which earlier
    targets are meant:

    * inside a modal ability - which earlier targets exist depends on the
      modes chosen;
    * under a condition - "if this spell was kicked, ... another target
      creature" is a target only a kicked spell has (CR 601.2c), and the
      engine asks for every target whether or not the condition holds.

    A verb sharing the target of the one before it (``same_target``) is not
    a new target; it is rewritten along with the one whose choice it shares.
    A player chosen as the target is not an object "other" could be
    distinct from.
    """
    from dataclasses import replace

    if not any(
        node.kind is EffectKind.CHOOSE_MODE or node.kind is EffectKind.CONDITIONAL
        for effect in effects
        for node in effect.walk()
    ):
        guarded = False
    else:
        guarded = True

    seen = 0
    rewritten: dict[int, object] = {}
    refused = False
    distinct_so_far = False

    def visit(node):
        nonlocal seen, refused, distinct_so_far
        if distinct_so_far and _refers_back(node):
            # "... up to one other target creature gets +1/+1. Those
            # creatures gain vigilance": the resolution remembers only what
            # the last instruction acted on, and "those creatures" means
            # every target. A singular "it" and a plural "those" read the
            # same here, so neither is read.
            refused = True
        if node.is_targeted and node.targets is not None and not node.targets_a_player:
            spec = node.targets
            if node.same_target:
                if id(spec) in rewritten:
                    node = replace(node, targets=rewritten[id(spec)])
            else:
                if spec.other_than_source and seen:
                    if guarded or node.kind is EffectKind.FIGHT:
                        # A fight's own subject is not held by the
                        # instruction, so what it is "other" than is not
                        # either.
                        refused = True
                    else:
                        new = replace(spec, other_than_source=False)
                        rewritten[id(spec)] = new
                        node = replace(
                            node, targets=new, distinct_from_earlier_targets=True
                        )
                        distinct_so_far = True
                seen += 1
        children = tuple(visit(child) for child in node.children)
        otherwise = tuple(visit(child) for child in node.otherwise)
        if any(a is not b for a, b in zip(children + otherwise, node.children + node.otherwise)):
            node = replace(node, children=children, otherwise=otherwise)
        return node

    out = tuple(visit(effect) for effect in effects)
    return None if refused else out


def _bound(text: str, ability: Ability, result: ParsedFace, *, rule: str) -> Ability:
    """The ability with its references settled: "its power" and "that
    card's mana value" to the object they mean (``object_referents``), "that
    player" and "its controller" to the player (``referents``) - or
    unreadable if the text does not say."""
    from .object_referents import settle_referents
    from .referents import Unbound, bind_ability

    shared = _share_targets(ability.effects)
    if shared is None:
        result.failures.append(
            ParseFailure(text, "one target shared across another", rule=rule)
        )
        return Ability.unreadable(text)
    from dataclasses import replace

    if any(a is not b for a, b in zip(shared, ability.effects)):
        ability = replace(ability, effects=shared)
    distinct = _other_than_earlier_targets(ability.effects)
    if distinct is not None and any(a is not b for a, b in zip(distinct, ability.effects)):
        ability = replace(ability, effects=distinct)
    if distinct is None:
        result.failures.append(
            ParseFailure(text, "'other target' after another target", rule=rule)
        )
        return Ability.unreadable(text)
    settled = settle_referents(ability)
    if settled is None:
        # "Its power" with no object the engine can read it off - the target
        # of the very instruction being carried out, or an object a delayed
        # ability no longer remembers. Answered off the source instead, it
        # would be a different card.
        result.failures.append(
            ParseFailure(text, "characteristic of an object it cannot name", rule=rule)
        )
        return Ability.unreadable(text)
    try:
        return bind_ability(settled)
    except Unbound as reason:
        result.failures.append(ParseFailure(text, str(reason), rule=rule))
        return Ability.unreadable(text)


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
