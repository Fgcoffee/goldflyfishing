"""Trigger conditions that must carry every word they read (CR 603.2).

Grammar-side checks; the board-side behaviour is in
``tests/rules/test_cr603_trigger_correctness.py``.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.query import PlayerScope


def read(text: str):
    stream = Stream.of(text)
    trigger = parse_trigger(stream)
    assert trigger is not None, text
    return trigger


# -- whose step -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "At the beginning of the end step, sacrifice this creature.",
    ],
)
def test_an_unowned_step_is_every_turn(text):
    assert read(text).players is None


def test_an_owned_step_keeps_its_owner():
    assert read("At the beginning of your end step, draw a card.").players.scope is PlayerScope.YOU
    assert read("At the beginning of combat on your turn, draw a card.").players.scope is PlayerScope.YOU


# -- counters ---------------------------------------------------------------


def test_the_counter_kind_is_kept():
    trigger = read("Whenever one or more -1/-1 counters are put on a creature, draw a card.")
    assert trigger.counter_kind == "-1/-1"
    assert not trigger.each_counter


def test_a_counter_is_each_counter():
    trigger = read("Whenever a +1/+1 counter is put on this creature, draw a card.")
    assert trigger.counter_kind == "+1/+1"
    assert trigger.each_counter


def test_word_counter_kinds_are_lower_case():
    trigger = read(
        "Whenever one or more loyalty counters are put on planeswalkers you control, draw a card."
    )
    assert trigger.counter_kind == "loyalty"


def test_becoming_monstrous_is_its_own_event():
    trigger = read("When this creature becomes monstrous, draw a card.")
    assert trigger.event_kinds == {EventKind.BECAME_MONSTROUS}
