"""Noun phrases and quantities: grammar tests, grouped by construct."""

from __future__ import annotations

import pytest

from mtgfish.parser.nouns import parse_object_filter, parse_value
from mtgfish.parser.tokens import Stream
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.query import PlayerScope, ValueKind


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _value(text):
    stream = Stream.of(text)
    value = parse_value(stream)
    assert value is not None and stream.done, f"{text!r} left {stream.remaining()}"
    return value


def _filter(text):
    stream = Stream.of(text)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done, f"{text!r} left {stream.remaining()}"
    return spec


# -- amounts the resolution produced (CR 608.2c) ------------------------------


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("the life lost this way", (EventKind.LIFE_LOST,)),
        ("the life gained this way", (EventKind.LIFE_GAINED,)),
        ("the damage dealt this way", (EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT)),
    ],
)
def test_amount_this_way(text, kinds):
    value = _value(text)
    assert value.kind is ValueKind.AMOUNT_THIS_WAY
    assert value.event_kinds == tuple(int(k) for k in kinds)


@pytest.mark.parametrize("text", ["the cards drawn this way", "the mana spent this way"])
def test_untallied_amounts_stay_unread(text):
    stream = Stream.of(text)
    value = parse_value(stream)
    assert value is None or not stream.done


# -- amounts over the turn ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "scope", "kind"),
    [
        ("the amount of life you gained this turn", PlayerScope.YOU, EventKind.LIFE_GAINED),
        ("the life you've lost this turn", PlayerScope.YOU, EventKind.LIFE_LOST),
        (
            "the total amount of life your opponents have lost this turn",
            PlayerScope.EACH_OPPONENT,
            EventKind.LIFE_LOST,
        ),
    ],
)
def test_life_this_turn_is_an_amount(text, scope, kind):
    value = _value(text)
    assert value.kind is ValueKind.EVENT_AMOUNT_THIS_TURN
    assert value.players.scope is scope
    assert value.event_kinds == (int(kind),)


def test_life_they_lost_this_turn_is_unread():
    stream = Stream.of("the amount of life they lost this turn")
    value = parse_value(stream)
    assert value is None or not stream.done
