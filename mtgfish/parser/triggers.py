"""Parsing trigger conditions (CR 603).

A trigger is "At/When/Whenever <event>, <effect>", and the comma is the join.
Finding the comma is not enough, though - "When this creature enters, if you
control three artifacts, draw a card" puts an intervening-if clause between
them, and CR 603.4 makes that clause load-bearing: it is checked when the
ability triggers *and* again when it resolves, and failing either time means
the ability does nothing.

The events themselves are a small, closed vocabulary, which is why this module
is a table rather than a grammar.
"""

from __future__ import annotations

from ..rules.cr600_spells_and_abilities.abilities import TriggerCondition
from ..rules.kernel.enums import CardType, Phase, Zone
from ..rules.kernel.events import EventKind
from ..rules.kernel.query import (
    ALWAYS,
    Condition,
    ConditionKind,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
)
from .nouns import parse_object_filter, parse_player_filter
from .tokens import Stream, TokenKind

SELF = ObjectFilter(source_only=True)
YOU = PlayerFilter(PlayerScope.YOU)

#: The nouns that can follow "this" and still mean the source object. Kept in
#: step with the same list in ``nouns``, because a card type missing from
#: either place silently turns a self-trigger into an unreadable one.
_SELF_NOUNS = (
    "creature", "permanent", "artifact", "enchantment", "land", "card",
    "spell", "planeswalker", "battle", "equipment", "aura", "vehicle",
    "token", "saga", "class", "case", "room", "creature's",
)

#: Phase and step triggers. "At the beginning of your upkeep" and its
#: relatives, which are by far the most common triggers in the corpus.
_STEP_TRIGGERS: tuple[tuple[str, EventKind], ...] = (
    ("upkeep", EventKind.UPKEEP),
    ("draw step", EventKind.DRAW_STEP),
    ("end step", EventKind.END_STEP),
    ("untap step", EventKind.UNTAP_STEP),
    ("first main phase", EventKind.PHASE_BEGAN),
    ("precombat main phase", EventKind.PHASE_BEGAN),
    ("postcombat main phase", EventKind.PHASE_BEGAN),
    ("second main phase", EventKind.PHASE_BEGAN),
    ("main phase", EventKind.PHASE_BEGAN),
    ("combat", EventKind.PHASE_BEGAN),
)


#: CR 500.1: which phase a phase trigger's phrase names. The phrases share
#: one event, so without this "at the beginning of combat" would fire as
#: every phase begins.
_MAIN_PHASES = frozenset({int(Phase.PRECOMBAT_MAIN), int(Phase.POSTCOMBAT_MAIN)})
_PHASES: dict[str, frozenset[int]] = {
    "first main phase": frozenset({int(Phase.PRECOMBAT_MAIN)}),
    "precombat main phase": frozenset({int(Phase.PRECOMBAT_MAIN)}),
    "postcombat main phase": frozenset({int(Phase.POSTCOMBAT_MAIN)}),
    "second main phase": frozenset({int(Phase.POSTCOMBAT_MAIN)}),
    "main phase": _MAIN_PHASES,
    "combat": frozenset({int(Phase.COMBAT)}),
}


def parse_trigger(stream: Stream) -> TriggerCondition | None:
    """The condition half of a triggered ability, up to and including its comma."""
    if not stream.accept("at", "when", "whenever"):
        return None

    condition = _step_trigger(stream) or _event_trigger(stream)
    if condition is None:
        return None

    intervening = _intervening_if(stream)
    if intervening is not None:
        condition = TriggerCondition(
            event_kinds=condition.event_kinds,
            subject=condition.subject,
            source=condition.source,
            players=condition.players,
            intervening_if=intervening,
            functions_in=condition.functions_in,
            uses_last_known_information=condition.uses_last_known_information,
            phases=condition.phases,
            text=condition.text,
        )

    stream.skip_punct(",")
    return condition


def parse_delayed_when(stream: Stream) -> TriggerCondition | None:
    """"at the beginning of the next end step", "at the beginning of the next
    turn's upkeep" - when a delayed trigger fires (CR 603.7).

    The same step vocabulary as an ordinary phase trigger, behind the words
    "the next", which is what makes it delayed rather than recurring.
    """
    mark = stream.mark()
    if not stream.accept_phrase("at the beginning of"):
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("the next turn's")
        or stream.accept_phrase("the next")
        or stream.accept_phrase("your next")
    ):
        stream.reset(mark)
        return None

    condition = _step_words(stream)
    if condition is None:
        stream.reset(mark)
        return None
    return condition


def _step_words(stream: Stream) -> TriggerCondition | None:
    """The step itself, shared with the ordinary phase-trigger reader."""
    from .tokens import Stream as _S  # noqa: F401  (documents the shared shape)

    for phrase, kind in (
        ("upkeep", EventKind.UPKEEP),
        ("draw step", EventKind.DRAW_STEP),
        ("end step", EventKind.END_STEP),
        ("cleanup step", EventKind.CLEANUP),
        ("untap step", EventKind.UNTAP_STEP),
        ("main phase", EventKind.PHASE_BEGAN),
        ("combat", EventKind.PHASE_BEGAN),
    ):
        if stream.accept_phrase(phrase):
            return TriggerCondition(
                event_kinds=frozenset({kind}),
                phases=_PHASES.get(phrase, frozenset()),
                text=f"at the next {phrase}",
            )
    return None


def _step_trigger(stream: Stream) -> TriggerCondition | None:
    """"At the beginning of your upkeep" and friends."""
    mark = stream.mark()
    if not stream.accept_phrase("the beginning of"):
        stream.reset(mark)
        return None

    # "your upkeep", "each opponent's upkeep", "each end step". The possessive
    # forms are the same player phrases with an apostrophe-s, which the
    # tokenizer keeps attached to the word.
    players = _step_owner(stream)
    if players is None:
        stream.accept("the")
        if stream.accept_phrase("next end step"):
            return TriggerCondition(
                event_kinds=frozenset({EventKind.END_STEP}),
                text="at the beginning of the next end step",
            )
        players = YOU
    stream.accept("next")

    for phrase, kind in _STEP_TRIGGERS:
        if stream.accept_phrase(phrase):
            return TriggerCondition(
                event_kinds=frozenset({kind}),
                players=players,
                phases=_PHASES.get(phrase, frozenset()),
                text=f"at the beginning of {phrase}",
            )

    stream.reset(mark)
    return None


#: Whose step it is, including the possessive forms oracle text uses in
#: "at the beginning of each opponent's upkeep".
_STEP_OWNERS: tuple[tuple[str, PlayerScope], ...] = (
    ("each opponent's", PlayerScope.EACH_OPPONENT),
    ("each other player's", PlayerScope.EACH_OPPONENT),
    ("each player's", PlayerScope.EACH_PLAYER),
    ("target opponent's", PlayerScope.TARGET_OPPONENT),
    ("target player's", PlayerScope.TARGET_PLAYER),
    ("an opponent's", PlayerScope.OPPONENT),
    ("each", PlayerScope.EACH_PLAYER),
    ("your", PlayerScope.YOU),
)


def _step_owner(stream: Stream) -> PlayerFilter | None:
    for phrase, scope in _STEP_OWNERS:
        if stream.accept_phrase(phrase):
            return PlayerFilter(scope)
    return parse_player_filter(stream)[0]


def _event_trigger(stream: Stream) -> TriggerCondition | None:
    """"Whenever <something> <does something>"."""
    mark = stream.mark()

    # "this creature enters" / "this permanent dies" - the source itself.
    # The noun varies with the card type and is decoration: after
    # normalization the card's own name is already "this", so anything
    # following it that names a permanent kind refers to the source.
    if stream.accept("this"):
        stream.accept(*_SELF_NOUNS)
        # "Whenever this creature or another creature you control dies" - the
        # source is one of two possible subjects, and reading only the first
        # half left the "or ..." stranded and failed the ability.
        widened = _or_another(stream)
        if widened is not None:
            return (
                _with_alternatives(_subject_event, stream, widened)
                or _reset(stream, mark)
            )
        return (
            _with_alternatives(lambda st, _s: _self_event(st), stream, None)
            or _reset(stream, mark)
        )

    # "one or more +1/+1 counters are put on ..." - the grammatical subject is
    # a pile of counters, which is not a noun phrase the object grammar can
    # read, so it needs its own path before the general one.
    counters = _counter_subject(stream)
    if counters is not None:
        return counters

    subject = parse_object_filter(stream)
    if subject is not None:
        return (
            _with_alternatives(_subject_event, stream, subject)
            or _reset(stream, mark)
        )

    players, _ = parse_player_filter(stream)
    if players is not None:
        return _player_event(stream, players) or _reset(stream, mark)

    stream.reset(mark)
    return None


def _with_alternatives(reader, stream: Stream, subject):
    """Read one event, then any number of "or <event>" alternatives.

    Disjunction is a general operator, not a fixed list of pairs. There was a
    table of compound events - "enters or attacks", "attacks or blocks" - and
    every phrasing outside it failed: "dies or is put into exile", "enters or
    is put into a graveyard", "attacks or becomes the target of a spell". The
    table can never be complete, because the operator combines freely.

    ``event_kinds`` is already a set, so an alternative costs nothing to
    represent: the trigger simply watches for more than one thing.
    """
    first = reader(stream, subject)
    if first is None:
        return None

    kinds = set(first.event_kinds)
    last_known = first.uses_last_known_information
    others: list = []
    while True:
        mark = stream.mark()
        # A three-way list punctuates: "dies, or is exiled, or leaves your
        # graveyard". Without the comma the second alternative was never
        # reached, and the whole ability failed on the word "or".
        stream.skip_punct(",")
        if not stream.accept("or"):
            stream.reset(mark)
            break
        alternative = reader(stream, subject)
        if alternative is None:
            # "or *a creature card* is put into a graveyard" - the alternative
            # names its own subject, so the reader bound to this one cannot
            # see it. A whole trigger is read instead and kept as a separate
            # alternative.
            stream.skip_punct(",")
            whole = _event_trigger(stream)
            if whole is not None:
                others.append(whole)
                continue
            stream.reset(mark)
            break
        # An alternative about a *different* subject cannot be folded into
        # one set of event kinds - the union would match combinations neither
        # half describes. It is kept whole instead, and the trigger becomes a
        # disjunction of complete conditions.
        if alternative.subject != first.subject:
            others.append(alternative)
            continue
        kinds |= alternative.event_kinds
        last_known = last_known or alternative.uses_last_known_information

    from dataclasses import replace as _replace

    if kinds != set(first.event_kinds) or last_known:
        first = _replace(
            first,
            event_kinds=frozenset(kinds),
            uses_last_known_information=last_known,
        )
    if not others:
        return first
    return _replace(first, alternatives=(first, *others))


def _or_another(stream: Stream) -> ObjectFilter | None:
    """"... or another creature you control" following "this creature".

    The union of "the source" and "another matching permanent" is exactly the
    filter *without* the source exclusion: "this creature or another creature
    you control" means every creature you control. Expressing it that way
    keeps one filter instead of needing a disjunction node the matcher would
    have to learn.
    """
    from dataclasses import replace

    mark = stream.mark()
    if not stream.accept("or"):
        return None
    other = parse_object_filter(stream)
    if other is None or not other.other_than_source:
        stream.reset(mark)
        return None
    return replace(other, other_than_source=False)


def _reset(stream: Stream, mark: int) -> None:
    stream.reset(mark)
    return None


def _self_event(stream: Stream) -> TriggerCondition | None:
    compound = _compound_event(stream)
    if compound is not None:
        return TriggerCondition(
            event_kinds=compound,
            subject=SELF,
            uses_last_known_information=EventKind.DIES in compound,
            text="when this does one of two things",
        )

    if stream.accept_phrase("enters the battlefield") or stream.accept("enters"):
        # "When this land enters *untapped*" - the state is part of the
        # trigger, and the two lands in the format that say it were failing
        # on the word.
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=_entry_state(stream, SELF),
            text="when this enters",
        )
    # "for the first time each turn" - the ordinal machinery, written the
    # long way round. Extra-combat cards use it to stop the loop being
    # infinite, so reading it as "whenever this attacks" would turn a bounded
    # effect into an unbounded one.
    #
    # The rewind point is *before* the verb: without the ordinal this is an
    # ordinary attack trigger and the general reader below has to see the
    # word, or "attacks or becomes the target of a spell" loses its first
    # alternative.
    before_verb = stream.mark()
    if stream.accept("attacks", "attack"):
        ordinal = 0
        if stream.accept_phrase("for the first time each turn"):
            ordinal = 1
        elif stream.accept_phrase("for the second time each turn"):
            ordinal = 2
        if ordinal:
            return TriggerCondition(
                event_kinds=frozenset({EventKind.ATTACKS}),
                subject=SELF,
                ordinal=ordinal,
                text="when this attacks for the nth time",
            )
    stream.reset(before_verb)

    look = stream.mark()
    if stream.accept("becomes"):
        if stream.accept("level"):
            level = stream.accept_number()
            if level is not None:
                from ..rules.kernel.query import (
                    Comparison,
                    NumericConstraint,
                    Value,
                )

                # CR 716.2. The level is checked as an intervening-if so the
                # ability fires on reaching *that* level and not on every
                # level after it.
                return TriggerCondition(
                    event_kinds=frozenset({EventKind.CLASS_LEVEL_GAINED}),
                    subject=SELF,
                    intervening_if=Condition(
                        kind=ConditionKind.CLASS_LEVEL,
                        constraint=NumericConstraint(
                            Comparison.GE, Value.of(level)
                        ),
                        text=f"level {level} or greater",
                    ),
                    text=f"when this becomes level {level}",
                )
    stream.reset(look)

    if stream.accept("dies"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DIES}),
            subject=SELF,
            uses_last_known_information=True,
            text="when this dies",
        )
    if stream.accept("attacks"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS}),
            subject=SELF,
            text="when this attacks",
        )
    if stream.accept("blocks", "block"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.BLOCKS}),
            subject=SELF,
            text="when this blocks",
        )
    if (
        stream.accept_phrase("leaves the battlefield")
        or stream.accept_phrase("leave the battlefield")
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LEAVES_BATTLEFIELD}),
            subject=SELF,
            uses_last_known_information=True,
            text="when this leaves the battlefield",
        )
    if stream.accept_phrase("becomes tapped"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TAPPED}),
            subject=SELF,
            text="when this becomes tapped",
        )
    if stream.accept_phrase("is turned face up"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TURNED_FACE_UP}),
            subject=SELF,
            text="when this is turned face up",
        )
    if (
        stream.accept_phrase("deals combat damage to a player")
        or stream.accept_phrase("deal combat damage to a player")
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
            source=SELF,
            text="when this deals combat damage to a player",
        )
    if stream.accept_phrase("deals damage"):
        # "deals damage to a player", "deals damage to an opponent", or with
        # nothing after it at all. The recipient narrows the trigger, so it is
        # read if it is there and left general if it is not.
        stream.accept("to")
        parse_player_filter(stream)
        parse_object_filter(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            source=SELF,
            text="when this deals damage",
        )
    if stream.accept_phrase("becomes the target of a spell or ability"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TARGETED}),
            subject=SELF,
            text="when this becomes the target of a spell or ability",
        )
    if stream.accept_phrase("becomes blocked"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.BECOMES_BLOCKED}),
            subject=SELF,
            text="when this becomes blocked",
        )
    if stream.accept_phrase("attacks or blocks"):
        # Two events in one trigger, which the engine handles natively -
        # ``event_kinds`` is a set precisely so an "or" costs nothing.
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS, EventKind.BLOCKS}),
            subject=SELF,
            text="when this attacks or blocks",
        )
    if stream.accept_phrase("becomes untapped"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.UNTAPPED}),
            subject=SELF,
            text="when this becomes untapped",
        )
    if stream.accept_phrase("becomes monstrous"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.COUNTER_ADDED}),
            subject=SELF,
            text="when this becomes monstrous",
        )
    if stream.accept_phrase("transforms into this"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TRANSFORMED}),
            subject=SELF,
            text="when this transforms",
        )

    # The same table the general-subject path uses. Kept shared rather than
    # duplicated: "is dealt damage" means the same thing whether the subject
    # is this permanent or any other, and two copies drift.
    simple = _simple_event(stream)
    if simple is not None:
        kinds, last_known = simple
        return TriggerCondition(
            event_kinds=kinds,
            subject=SELF,
            uses_last_known_information=last_known,
            text="when this has something happen to it",
        )
    return None


#: Events written as one phrase covering two triggers. They have to be tried
#: before the single-verb forms, or "enters" consumes the first half of
#: "enters or attacks" and strands the rest - which fails an ability the
#: grammar otherwise understands completely.
_COMPOUND_EVENTS: tuple[tuple[str, tuple[EventKind, ...]], ...] = (
    ("enters or attacks", (EventKind.ENTERS_BATTLEFIELD, EventKind.ATTACKS)),
    ("enters or dies", (EventKind.ENTERS_BATTLEFIELD, EventKind.DIES)),
    ("attacks or blocks", (EventKind.ATTACKS, EventKind.BLOCKS)),
    ("blocks or becomes blocked", (EventKind.BLOCKS, EventKind.BECOMES_BLOCKED)),
    ("attacks or becomes blocked", (EventKind.ATTACKS, EventKind.BECOMES_BLOCKED)),
)


def _compound_event(stream: Stream) -> frozenset[EventKind] | None:
    for phrase, kinds in _COMPOUND_EVENTS:
        if stream.accept_phrase(phrase):
            return frozenset(kinds)
    return None


def _subject_event(stream: Stream, subject: ObjectFilter) -> TriggerCondition | None:
    """"Whenever a creature you control dies" and its family."""
    compound = _compound_event(stream)
    if compound is not None:
        return TriggerCondition(
            event_kinds=compound,
            subject=subject,
            uses_last_known_information=EventKind.DIES in compound,
            text="whenever something does one of two things",
        )

    if stream.accept_phrase("enters the battlefield") or stream.accept(
        "enters", "entered", "enter"
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=subject,
            text="whenever something enters",
        )
    if stream.accept("dies", "die"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DIES}),
            subject=subject,
            uses_last_known_information=True,
            text="whenever something dies",
        )
    if stream.accept("attacks", "attack"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS}),
            subject=subject,
            text="whenever something attacks",
        )
    for_mana = _tapped_for_mana(stream, subject)
    if for_mana is not None:
        return for_mana

    if (
        stream.accept_phrase("is put into a graveyard")
        or stream.accept_phrase("are put into a graveyard")
        or stream.accept_phrase("is put into your graveyard")
        or stream.accept_phrase("are put into your graveyard")
        or stream.accept_phrase("is put into their graveyard")
        or stream.accept_phrase("are put into their graveyard")
    ):
        # "from the battlefield" is a different event - that is what dying
        # is - and the rest narrow the same one. Which is why the origin has
        # to be read here rather than left for the effect grammar to trip on.
        kind = EventKind.PUT_INTO_GRAVEYARD
        if stream.accept_phrase("from the battlefield"):
            kind = EventKind.DIES
        else:
            _graveyard_origin(stream)
        return TriggerCondition(
            event_kinds=frozenset({kind}),
            subject=subject,
            uses_last_known_information=True,
            text="whenever something is put into a graveyard",
        )
    if (
        stream.accept_phrase("leaves the battlefield")
        or stream.accept_phrase("leave the battlefield")
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LEAVES_BATTLEFIELD}),
            subject=subject,
            uses_last_known_information=True,
            text="whenever something leaves the battlefield",
        )
    if stream.accept_phrase("is cast") or stream.accept("cast"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.CAST_SPELL}),
            subject=subject,
            functions_in=frozenset({Zone.BATTLEFIELD, Zone.STACK}),
            text="whenever something is cast",
        )
    if stream.accept("blocks", "block"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.BLOCKS}),
            subject=subject,
            text="whenever something blocks",
        )
    if stream.accept_phrase("becomes tapped"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TAPPED}),
            subject=subject,
            text="whenever something becomes tapped",
        )
    if (
        stream.accept_phrase("deals combat damage to a player")
        or stream.accept_phrase("deal combat damage to a player")
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
            source=subject,
            text="whenever something deals combat damage to a player",
        )
    if stream.accept_phrase("deals damage"):
        stream.accept("to")
        parse_player_filter(stream)
        parse_object_filter(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            source=subject,
            text="whenever something deals damage",
        )

    zoned = _leaves_zone(stream, subject)
    if zoned is not None:
        return zoned

    counters = _counters_placed(stream, subject)
    if counters is not None:
        return counters

    simple = _simple_event(stream)
    if simple is not None:
        kinds, last_known = simple
        return TriggerCondition(
            event_kinds=kinds,
            subject=subject,
            uses_last_known_information=last_known,
            text="whenever something happens to it",
        )
    return None


def _entry_state(stream: Stream, subject):
    """"When this land enters *untapped*" - a state the entry has to satisfy.

    CR 603.4 makes this an intervening-if in all but spelling: the trigger
    fires only if the permanent entered in that state. Expressed as a
    constraint on the subject, which the matcher can already test.
    """
    from dataclasses import replace

    if stream.accept("untapped"):
        return replace(subject, tapped=False) if subject is not None else None
    if stream.accept("tapped"):
        return replace(subject, tapped=True) if subject is not None else None
    return subject


def _tapped_for_mana(stream: Stream, subject):
    """"is tapped for mana", "you tap a permanent for {C}".

    Both are the same event - mana was produced by tapping something - and
    the engine emits it already. The mana-doubling lands and every "adds an
    additional" enchantment in the format depend on it.
    """
    mark = stream.mark()
    if not (
        stream.accept_phrase("is tapped for mana")
        or stream.accept_phrase("are tapped for mana")
        or stream.accept_phrase("is tapped for")
        or stream.accept_phrase("are tapped for")
    ):
        stream.reset(mark)
        return None
    # "tapped for {C}" names which mana; the trigger fires on the production
    # either way and the symbol narrows nothing the engine records.
    while stream.peek().kind is TokenKind.SYMBOL:
        stream.next()
    stream.accept("mana")

    return TriggerCondition(
        event_kinds=frozenset({EventKind.MANA_ADDED}),
        subject=subject,
        text="whenever something is tapped for mana",
    )


def _graveyard_origin(stream: Stream) -> None:
    """"from anywhere", "from anywhere other than the battlefield".

    Every origin except the battlefield names the same event, so these
    narrow nothing the trigger has to record - but they have to be read, or
    the words fall through to the effect grammar and fail the ability.
    """
    mark = stream.mark()
    if not stream.accept("from"):
        return
    if stream.accept_phrase("anywhere other than the battlefield") or stream.accept(
        "anywhere"
    ):
        return
    from .nouns import parse_zone

    if parse_zone(stream) is not None:
        return
    stream.reset(mark)


def _counter_subject(stream: Stream):
    """"Whenever one or more +1/+1 counters are put on <something>"."""
    from .tokens import TokenKind

    mark = stream.mark()
    stream.accept_number()
    stream.accept_phrase("or more")

    kind = ""
    token = stream.peek()
    if token.kind is TokenKind.PT:
        stream.next()
        kind = token.text
    elif token.kind is TokenKind.WORD and stream.peek(1).lower in (
        "counter",
        "counters",
    ):
        stream.next()
        kind = token.text

    if not kind or not stream.accept("counter", "counters"):
        stream.reset(mark)
        return None

    result = _counters_placed(stream, SELF)
    if result is None:
        stream.reset(mark)
        return None
    return result


def _leaves_zone(stream: Stream, subject: ObjectFilter):
    """"... leave your graveyard", "... enters your graveyard from anywhere".

    The battlefield forms are handled above with their own event kinds; this
    is the general zone change, which is what "one or more cards leave your
    graveyard" is asking about.
    """
    from dataclasses import replace

    mark = stream.mark()
    if not stream.accept("leave", "leaves"):
        stream.reset(mark)
        return None

    from .nouns import parse_zone

    stream.accept("your", "their", "his", "her", "a", "an", "the")
    zone = parse_zone(stream)
    if zone is None:
        stream.reset(mark)
        return None

    # The subject is a card *in that zone* right up until it leaves, so the
    # filter has to say so - a graveyard filter tested against the battlefield
    # matches nothing and the trigger never fires.
    return TriggerCondition(
        event_kinds=frozenset({EventKind.ZONE_CHANGE}),
        subject=replace(subject, zones=frozenset({zone})),
        functions_in=frozenset({Zone.BATTLEFIELD}),
        uses_last_known_information=True,
        text="whenever something leaves a zone",
    )


def _counters_placed(stream: Stream, subject: ObjectFilter):
    """"Whenever one or more +1/+1 counters are put on this creature"."""
    mark = stream.mark()
    if not (
        stream.accept_phrase("are put on")
        or stream.accept_phrase("is put on")
        or stream.accept_phrase("are placed on")
    ):
        stream.reset(mark)
        return None

    target = parse_object_filter(stream)
    if target is None:
        stream.reset(mark)
        return None
    # The *subject* of the sentence is the counters; the trigger is about the
    # permanent they land on, which is what the engine's event carries.
    return TriggerCondition(
        event_kinds=frozenset({EventKind.COUNTER_ADDED}),
        subject=target,
        text="whenever counters are put on something",
    )


#: Event phrasings that name the affected object rather than the actor. Every
#: one of these EventKinds is already emitted by the engine - the grammar
#: simply had no way to ask for them, which is why more than a thousand
#: abilities beginning "Whenever" gave up on their very first clause.
#:
#: Each entry is (phrase, event kinds, uses-last-known-information).
_SIMPLE_EVENTS: tuple[tuple[str, tuple[EventKind, ...], bool], ...] = (
    ("is dealt damage", (EventKind.DAMAGE_DEALT,), False),
    ("are dealt damage", (EventKind.DAMAGE_DEALT,), False),
    ("becomes the target", (EventKind.TARGETED,), False),
    ("is targeted", (EventKind.TARGETED,), False),
    ("becomes blocked", (EventKind.BECOMES_BLOCKED,), False),
    ("becomes untapped", (EventKind.UNTAPPED,), False),
    ("becomes attached", (EventKind.ATTACHED,), False),
    ("is turned face up", (EventKind.TURNED_FACE_UP,), False),
    ("is turned face down", (EventKind.TURNED_FACE_DOWN,), False),
    ("is put into a graveyard from the battlefield", (EventKind.DIES,), True),
    ("is put into a graveyard from anywhere", (EventKind.PUT_INTO_GRAVEYARD,), True),
    ("is put into a graveyard", (EventKind.PUT_INTO_GRAVEYARD,), True),
    ("are put into a graveyard", (EventKind.PUT_INTO_GRAVEYARD,), True),
    ("is put into exile", (EventKind.EXILED,), True),
    ("is put onto the battlefield", (EventKind.ENTERS_BATTLEFIELD,), False),
    ("is sacrificed", (EventKind.SACRIFICED,), True),
    ("is destroyed", (EventKind.DESTROYED,), True),
    ("is exiled", (EventKind.EXILED,), True),
    ("is countered", (EventKind.COUNTERED,), False),
    ("is transformed", (EventKind.TRANSFORMED,), False),
    ("transforms", (EventKind.TRANSFORMED,), False),
    ("phases out", (EventKind.PHASED_OUT,), False),
    ("phases in", (EventKind.PHASED_IN,), False),
    ("is returned to its owner's hand", (EventKind.RETURNED_TO_HAND,), True),
    ("becomes monstrous", (EventKind.COUNTER_ADDED,), False),
    ("untaps", (EventKind.UNTAPPED,), False),
    ("taps", (EventKind.TAPPED,), False),
    ("attacks or blocks", (EventKind.ATTACKS, EventKind.BLOCKS), False),
    ("blocks or becomes blocked", (EventKind.BLOCKS, EventKind.BECOMES_BLOCKED), False),
    ("enters or attacks", (EventKind.ENTERS_BATTLEFIELD, EventKind.ATTACKS), False),
    ("enters or dies", (EventKind.ENTERS_BATTLEFIELD, EventKind.DIES), True),
    ("attacks or dies", (EventKind.ATTACKS, EventKind.DIES), True),
)


def _simple_event(stream: Stream):
    """One of the phrasings in ``_SIMPLE_EVENTS``, longest first.

    Longest first because "attacks or blocks" and "attacks" both start the
    same way, and matching the short one would leave "or blocks" stranded and
    fail the whole ability.
    """
    for phrase, kinds, last_known in sorted(
        _SIMPLE_EVENTS, key=lambda entry: -len(entry[0])
    ):
        if not stream.accept_phrase(phrase):
            continue
        # "becomes the target *of a spell or ability*", "is dealt damage *by a
        # source you control*". The tail says what acted; consumed rather than
        # modelled, because narrowing it needs a source constraint the event
        # does not always carry.
        look = stream.mark()
        if stream.accept("of", "by"):
            if parse_object_filter(stream) is None and not _ability_words(stream):
                stream.reset(look)
        return frozenset(kinds), last_known
    return None


def _ability_words(stream: Stream) -> bool:
    """"a spell or ability" written out, which is not an object filter."""
    mark = stream.mark()
    stream.accept("a", "an")
    if stream.accept("spell"):
        if stream.accept("or"):
            stream.accept("ability")
        return True
    if stream.accept("ability"):
        return True
    stream.reset(mark)
    return False


#: "their second spell", "their first noncreature spell" - the ordinal words
#: cards actually use. Beyond the third there are no printed examples.
_ORDINALS = {"first": 1, "second": 2, "third": 3}


def _ordinal_prefix(stream: Stream) -> int:
    """"their second ..." - which occurrence of the event this turn fires it.

    Read before the noun, and paired with the "each turn" that always closes
    the phrase. Returns 0 when there is no ordinal, which means every
    occurrence.
    """
    mark = stream.mark()
    if not stream.accept("their", "his", "her", "your", "its"):
        return 0
    word = stream.peek().lower
    if word not in _ORDINALS:
        stream.reset(mark)
        return 0
    stream.next()
    return _ORDINALS[word]


def _each_turn_tail(stream: Stream) -> None:
    """The "each turn" that closes an ordinal trigger, and its variants."""
    (
        stream.accept_phrase("each turn")
        or stream.accept_phrase("this turn")
        or stream.accept_phrase("each of their turns")
    )


def _player_event(stream: Stream, players: PlayerFilter) -> TriggerCondition | None:
    # Oracle text conjugates for the subject: "you gain" but "a player gains".
    # Both forms mean the same event, so both are accepted everywhere.
    if stream.accept_phrase("gain life") or stream.accept_phrase("gains life"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LIFE_GAINED}),
            players=players,
            text="whenever a player gains life",
        )
    if stream.accept_phrase("lose life") or stream.accept_phrase("loses life"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LIFE_LOST}),
            players=players,
            text="whenever a player loses life",
        )
    if (
        stream.accept_phrase("draw a card")
        or stream.accept_phrase("draws a card")
        or stream.accept("draw", "draws")
    ):
        # "draws *their second card* each turn" - the same event, restricted
        # to one occurrence per turn.
        ordinal = _ordinal_prefix(stream)
        if ordinal:
            stream.accept("card", "cards")
            _each_turn_tail(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DREW_CARD}),
            players=players,
            ordinal=ordinal,
            text="whenever a player draws",
        )
    # Deliberately the bare verb rather than "cast a spell": the fixed phrase
    # matched first and swallowed the noun, so "casts a spell *of the chosen
    # type*" could never extend it and every such trigger failed on "of".
    if stream.accept("cast", "casts"):
        # "Whenever you cast *or copy* an instant or sorcery spell" - one
        # trigger watching two events. Reading only "cast" left the rest of
        # the sentence stranded; and because the trigger itself matched, the
        # failure surfaced as an unreadable *effect*, which points at the
        # wrong half of the card.
        kinds = {EventKind.CAST_SPELL}
        look = stream.mark()
        if stream.accept("or"):
            if stream.accept("copy", "copies"):
                kinds.add(EventKind.COPIED)
            else:
                stream.reset(look)

        # "casts *their second spell* each turn" - the commonest ordinal in
        # the format, and the reason a whole family of tax and draw engines
        # could not be read.
        ordinal = _ordinal_prefix(stream)
        spec = parse_object_filter(stream) or ObjectFilter(
            zones=frozenset({Zone.STACK})
        )
        if ordinal:
            _each_turn_tail(stream)
        return TriggerCondition(
            event_kinds=frozenset(kinds),
            subject=spec,
            players=players,
            ordinal=ordinal,
            text="whenever a player casts a spell",
        )
    look = stream.mark()
    if stream.accept("tap", "taps"):
        # "Whenever you tap a permanent for {C}" - the player is the one
        # doing the tapping and the permanent is what produced the mana.
        tapped = parse_object_filter(stream)
        if tapped is not None and stream.accept("for"):
            while stream.peek().kind is TokenKind.SYMBOL:
                stream.next()
            stream.accept("mana")
            return TriggerCondition(
                event_kinds=frozenset({EventKind.MANA_ADDED}),
                subject=tapped,
                players=players,
                text="whenever a player taps something for mana",
            )
        stream.reset(look)

    if stream.accept_phrase("searches their library") or stream.accept_phrase(
        "search their library"
    ):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.SEARCHED_LIBRARY}),
            players=players,
            text="whenever a player searches their library",
        )

    if stream.accept("create", "creates"):
        # "Whenever you create *or sacrifice* a token" - two different things
        # happening to the same object, which is what the alternatives
        # machinery is for, but the verbs are the player's rather than the
        # object's so they are read here.
        kinds = {EventKind.TOKEN_CREATED}
        look = stream.mark()
        if stream.accept("or"):
            if stream.accept("sacrifice", "sacrifices"):
                kinds.add(EventKind.SACRIFICED)
            else:
                stream.reset(look)
        spec = parse_object_filter(stream)
        if spec is not None:
            return TriggerCondition(
                event_kinds=frozenset(kinds),
                subject=spec,
                players=players,
                text="whenever a player creates or sacrifices something",
            )
        stream.reset(look)

    if stream.accept("attack", "attacks"):
        # "Whenever you attack" (CR 506.2, as templated) means the declaration
        # happened, not that any particular creature did - so the trigger
        # watches the declaration event rather than an attacker.
        parse_object_filter(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKERS_DECLARED}),
            players=players,
            text="whenever a player attacks",
        )
    if stream.accept("discard", "discards"):
        parse_object_filter(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DISCARDED}),
            players=players,
            text="whenever a player discards",
        )
    if stream.accept("sacrifice", "sacrifices"):
        spec = parse_object_filter(stream)
        return TriggerCondition(
            event_kinds=frozenset({EventKind.SACRIFICED}),
            subject=spec,
            players=players,
            uses_last_known_information=True,
            text="whenever a player sacrifices something",
        )
    if stream.accept("tap", "taps"):
        spec = parse_object_filter(stream)
        stream.accept_phrase("for mana")
        return TriggerCondition(
            event_kinds=frozenset({EventKind.TAPPED}),
            subject=spec,
            players=players,
            text="whenever a player taps something",
        )
    if stream.accept_phrase("draws a card") or stream.accept("draws"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.DREW_CARD}),
            players=players,
            text="whenever a player draws",
        )
    if stream.accept_phrase("gains life"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LIFE_GAINED}),
            players=players,
            text="whenever a player gains life",
        )
    if stream.accept_phrase("loses life"):
        return TriggerCondition(
            event_kinds=frozenset({EventKind.LIFE_LOST}),
            players=players,
            text="whenever a player loses life",
        )
    if stream.accept_phrase("casts a spell") or stream.accept("casts"):
        spec = parse_object_filter(stream) or ObjectFilter(
            zones=frozenset({Zone.STACK})
        )
        return TriggerCondition(
            event_kinds=frozenset({EventKind.CAST_SPELL}),
            subject=spec,
            players=players,
            text="whenever a player casts a spell",
        )
    return None


def _intervening_if(stream: Stream) -> Condition | None:
    """CR 603.4: "..., if <condition>, <effect>".

    Only a handful of shapes appear often enough to be worth reading, and an
    unreadable one has to fail the whole ability rather than be dropped -
    dropping it would make the ability fire when the card says it should not.
    """
    mark = stream.mark()
    stream.skip_punct(",")
    if not stream.accept("if"):
        stream.reset(mark)
        return None

    condition = _count_condition(stream)
    if condition is None:
        # Unreadable: rewind, so the caller's full-consumption check sees the
        # leftover "if ..." and fails the ability honestly.
        stream.reset(mark)
        return None

    stream.skip_punct(",")
    return condition


def _count_condition(stream: Stream) -> Condition | None:
    """"if you control three or more artifacts", "if you control no Thopters"."""
    mark = stream.mark()

    if stream.accept_phrase("you control no"):
        # "no X" is "zero or more X" inverted, and reading it as the positive
        # form would make the ability fire exactly when it must not.
        spec = parse_object_filter(stream)
        if spec is None:
            stream.reset(mark)
            return None
        return Condition(
            kind=ConditionKind.CONTROLS_MATCHING,
            filter=_owned(spec),
            constraint=NumericConstraint.exactly(0),
            text="if you control no ...",
        )

    if stream.accept_phrase("you control"):
        spec = parse_object_filter(stream)
        if spec is None:
            stream.reset(mark)
            return None
        constraint = spec.count
        return Condition(
            kind=ConditionKind.CONTROLS_MATCHING,
            filter=_owned(spec),
            constraint=NumericConstraint.at_least(
                constraint.constant if constraint is not None else 1
            ),
            text="if you control ...",
        )

    # "if no mana was spent to cast it" - a fact about how the spell was cast,
    # which the spell carries. Free spells are the whole point of the card,
    # and a condition read as always-true would counter everything.
    if stream.accept_phrase("no mana was spent to cast it"):
        return Condition(
            kind=ConditionKind.NO_MANA_SPENT, text="if no mana was spent to cast it"
        )

    if stream.accept_phrase("it's your turn"):
        return Condition(kind=ConditionKind.IS_YOUR_TURN, text="if it's your turn")

    if stream.accept_phrase("it's not your turn"):
        return Condition(
            kind=ConditionKind.NOT,
            operands=(Condition(kind=ConditionKind.IS_YOUR_TURN),),
            text="if it's not your turn",
        )

    stream.reset(mark)
    return None


def _owned(spec: ObjectFilter) -> ObjectFilter:
    """The same filter, narrowed to permanents you control.

    Rebuilt rather than ``replace``d because the phrase "you control" already
    consumed the ownership words, so the parsed filter has no controller set
    and inheriting one from elsewhere would be wrong.
    """
    from dataclasses import replace

    return replace(spec, controller=ControllerRelation.YOU, count=None)


#: Exported for the tests that check the parser only builds triggers the
#: engine's trigger matcher understands.
KNOWN_EVENTS = frozenset(
    kind
    for _, kind in _STEP_TRIGGERS
) | {
    EventKind.ENTERS_BATTLEFIELD,
    EventKind.DIES,
    EventKind.ATTACKS,
    EventKind.BLOCKS,
    EventKind.LEAVES_BATTLEFIELD,
    EventKind.TAPPED,
    EventKind.TURNED_FACE_UP,
    EventKind.COMBAT_DAMAGE_DEALT,
    EventKind.PUT_INTO_GRAVEYARD,
    EventKind.CAST_SPELL,
    EventKind.DREW_CARD,
    EventKind.LIFE_GAINED,
    EventKind.LIFE_LOST,
}

_ = ALWAYS, CardType  # referenced by the condition vocabulary above
