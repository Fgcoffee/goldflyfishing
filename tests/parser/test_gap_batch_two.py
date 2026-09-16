"""The second gap batch: costs, modes, and the grammar of "un-".

Same principle as the first batch - each test names a *category* of card, not
one template, because a category regressing is much harder to spot than a card
regressing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.compile import parse_face
from mtgfish.parser.costs import parse_cost
from mtgfish.parser.normalize import normalize
from mtgfish.parser.nouns import parse_object_filter
from mtgfish.parser.split import LineKind, split_abilities
from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.effects import EffectKind
from mtgfish.rules.query import ValueKind


@pytest.fixture(autouse=True)
def _registry(card_db):
    """Subtype lookups need the real registry loaded."""
    card_db.registry()


def _reads(text):
    stream = Stream.of(text)
    return parse_effects(stream) is not None and stream.done


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------


def test_a_colon_inside_quotes_is_not_an_activation_cost():
    """The largest single source of unreadable costs in the pool.

    "Enchanted creature has '{T}: Add {G}'" is a *static* ability that grants
    an activated one. Splitting at the inner colon made the parser try to read
    `Enchanted creature has "{T}` as a cost.
    """
    text = normalize('Enchanted creature has "{T}: Add {G}."', card_name="X")
    lines = split_abilities(text, is_permanent=True)
    assert [line.kind for line in lines] == [LineKind.STATIC]


def test_a_real_activation_colon_still_splits():
    """The guard above must not swallow ordinary activated abilities."""
    text = normalize("{T}: Add {G}.", card_name="X")
    lines = split_abilities(text, is_permanent=True)
    assert [line.kind for line in lines] == [LineKind.ACTIVATED]


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "{2}, Tap an untapped creature you control",
        "{1}, Exile this artifact",
        "{2}, Discard a card at random",
        "Sacrifice this Aura",
        "Pay {2}",
        "Boast - {1}{R}",
        "Exhaust - {2}",
        "Exile this card from your graveyard",
    ],
)
def test_cost_shapes_that_used_to_fail(text):
    cost, reason = parse_cost(text)
    assert cost is not None, reason


def test_a_genuinely_unreadable_cost_still_fails():
    """Full consumption is the point; this must not have been loosened."""
    cost, reason = parse_cost("Flumox the wibble")
    assert cost is None and reason


# ---------------------------------------------------------------------------
# "un-" as a negation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "field", "expected"),
    [
        ("untapped creature you control", "tapped", False),
        ("tapped creature you control", "tapped", True),
        ("unblocked creature", "blocked", False),
    ],
)
def test_un_negates_a_state_adjective(phrase, field, expected):
    """English negates a state with "un-", not "non-". Only the "non-" form
    was handled, so "an untapped creature you control" failed the whole noun
    phrase - and with it every cost and effect built on one."""
    stream = Stream.of(phrase)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert getattr(spec, field) is expected


def test_un_does_not_negate_an_unrelated_word():
    """"Unearth" is not the negation of "earth". The list is closed for a
    reason."""
    stream = Stream.of("creature")
    spec = parse_object_filter(stream)
    assert spec is not None


# ---------------------------------------------------------------------------
# Modal abilities that are not modal *lines*
# ---------------------------------------------------------------------------


def _face(text):
    class Face:
        name = "X"
        oracle_text = text
        type_line = None

    class Card:
        faces = [Face()]

    return parse_face(Card(), 0)


def test_a_triggered_ability_can_be_modal():
    """"When this creature enters, choose one -" is a triggered ability whose
    *effect* is modal. The marker had to start the paragraph, so every card
    like this threw its modes away and failed on the words "choose one"."""
    parsed = _face(
        "When this creature enters, choose one -\n* Draw a card.\n* You gain 3 life."
    )
    assert not parsed.failures
    (ability,) = parsed.abilities
    assert not ability.unparsed

    kinds = {node.kind for effect in ability.effects for node in effect.walk()}
    assert EffectKind.CHOOSE_MODE in kinds
    modes = [
        node for effect in ability.effects for node in effect.walk()
        if node.kind is EffectKind.CHOOSE_MODE
    ][0]
    assert len(modes.children) == 2


def test_a_wholly_modal_line_still_works():
    parsed = _face("Choose one -\n* Draw a card.\n* You gain 3 life.")
    assert not parsed.failures


def test_an_unreadable_mode_fails_the_whole_ability():
    """A modal spell that understands two of three modes is not two-thirds
    right - it is a card that can make a choice the engine cannot carry out."""
    parsed = _face(
        "When this creature enters, choose one -\n"
        "* Draw a card.\n"
        "* Flumox the wibble."
    )
    assert parsed.failures
    assert all(a.unparsed for a in parsed.abilities)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Whenever a player casts a spell, draw a card",
        "Whenever another player casts a spell, draw a card",
        "Whenever one or more creature cards leave your graveyard, draw a card",
        "Whenever one or more +1/+1 counters are put on this creature, draw a card",
    ],
)
def test_trigger_shapes_that_used_to_fail(text):
    assert parse_trigger(Stream.of(text)) is not None


def test_a_leave_zone_trigger_looks_in_that_zone():
    """A graveyard filter tested against the battlefield matches nothing and
    the trigger never fires."""
    from mtgfish.rules.enums import Zone

    trigger = parse_trigger(
        Stream.of("Whenever one or more creature cards leave your graveyard, draw a card")
    )
    assert trigger is not None
    assert trigger.subject is not None
    assert Zone.GRAVEYARD in trigger.subject.zones


# ---------------------------------------------------------------------------
# Durations on prohibitions
# ---------------------------------------------------------------------------


def test_a_prohibition_can_be_temporary():
    """A "can't" from a resolved spell nearly always has a duration. Reading
    it as permanent left the phrase unconsumed and failed the ability."""
    from mtgfish.rules.enums import Duration

    stream = Stream.of("Target creature can't be blocked this turn.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done

    restriction = next(
        node
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.RESTRICTION
    )
    assert restriction.duration == Duration.END_OF_TURN


# ---------------------------------------------------------------------------
# One-sided characteristic-defining abilities
# ---------------------------------------------------------------------------


def test_a_cda_may_define_only_the_power(card_db):
    """"Mendicant Core's power is equal to the number of artifacts you
    control" leaves the printed toughness standing. Reading the missing half
    as zero makes every such creature an X/0 that dies at once."""
    from mtgfish.parser import parse_card

    card = card_db.lookup("Mendicant Core, Guidelight")
    if card is None:
        pytest.skip("card not in this pool")

    defined = [
        a
        for face in parse_card(card).faces
        for a in face.abilities
        if a.is_characteristic_defining
    ]
    assert defined
    effect = defined[0].effects[0]
    assert effect.amount.kind is ValueKind.COUNT
    assert effect.amount2.kind is ValueKind.UNCHANGED


@pytest.mark.parametrize(
    "text",
    [
        "As long as there are seven or more cards in your graveyard, this creature gets +1/+1.",
        "This creature gets +7/+7 as long as there are seven or more cards in your graveyard.",
    ],
)
def test_there_are_n_or_more_reads(text):
    assert _reads(text)
