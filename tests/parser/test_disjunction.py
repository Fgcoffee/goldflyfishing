"""Disjunction as a general operator.

It was implemented for bare card types and nothing else, so every other place
"or" joins two things failed. The fix in each case has the same shape:
whatever reads one term should read a list of them.

The trigger case is the one worth keeping in mind. There was a table of
compound events - "enters or attacks", "attacks or blocks" - and every
phrasing outside it failed. A table of pairs can never be complete, because
the operator combines freely; ``event_kinds`` is already a set, so an
alternative costs nothing to represent.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.nouns import parse_object_filter
from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.kernel.enums import CardType


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _reads(text):
    stream = Stream.of(text)
    return parse_effects(stream) is not None and stream.done


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "When this artifact enters or is put into a graveyard from the "
            "battlefield, draw a card",
            {"ENTERS_BATTLEFIELD", "DIES"},
        ),
        (
            "When enchanted permanent dies or is put into exile, draw a card",
            {"DIES", "EXILED"},
        ),
        (
            "Whenever this creature attacks or becomes the target of a spell, "
            "draw a card",
            {"ATTACKS", "TARGETED"},
        ),
        (
            "Whenever this creature attacks or blocks, draw a card",
            {"ATTACKS", "BLOCKS"},
        ),
    ],
)
def test_a_trigger_may_watch_several_events(text, expected):
    trigger = parse_trigger(Stream.of(text))
    assert trigger is not None
    assert {k.name for k in trigger.event_kinds} == expected


def test_a_single_event_trigger_is_unchanged():
    trigger = parse_trigger(Stream.of("When this creature dies, draw a card"))
    assert trigger is not None
    assert {k.name for k in trigger.event_kinds} == {"DIES"}


def test_an_alternative_about_a_different_subject_is_refused():
    """Better to stop and let full consumption report the ability than to
    silently widen the first trigger to something it does not watch."""
    stream = Stream.of(
        "Whenever this creature dies or another artifact you control is put "
        "into a graveyard, draw a card"
    )
    trigger = parse_trigger(stream)
    assert trigger is not None
    assert stream.remaining(), "the mismatched half must be left unread"


# ---------------------------------------------------------------------------
# Nouns
# ---------------------------------------------------------------------------


def test_an_alternative_may_carry_a_qualifier():
    """"artifact, enchantment, or *nonbasic* land" - reading only a bare type
    word ended the list one item early."""
    stream = Stream.of("artifact, enchantment, or nonbasic land")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.types_any & CardType.LAND


def test_an_alternative_may_carry_a_negated_type():
    stream = Stream.of("noncreature artifact or noncreature enchantment")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.types_none & CardType.CREATURE


def test_a_disjunction_may_span_a_player_and_an_object():
    """CR 115.4 models "or player" as a flag on the filter, so the two halves
    collapse whichever order the card writes them."""
    stream = Stream.of("target player or planeswalker")
    spec = parse_object_filter(stream)
    if spec is None:
        from mtgfish.parser.nouns import parse_target

        stream = Stream.of("target player or planeswalker")
        spec, _ = parse_target(stream)
    assert spec is not None and stream.done
    assert spec.includes_players


# ---------------------------------------------------------------------------
# Prohibitions
# ---------------------------------------------------------------------------


def test_a_prohibition_may_name_several_acts():
    """"can't attack or block" and "can't attack and can't block" are the same
    card. Requiring the second "can't" stopped the shorter, commoner form
    after the first act."""
    assert _reads("This creature can't attack or block.")
    assert _reads("This creature can't attack.")


@pytest.mark.parametrize(
    "text",
    [
        "Destroy target artifact, enchantment, or nonbasic land an opponent controls.",
        "this deals 4 damage to target player or planeswalker.",
        "Exile target noncreature artifact or noncreature enchantment.",
    ],
)
def test_real_disjunction_shapes(text):
    assert _reads(text)
