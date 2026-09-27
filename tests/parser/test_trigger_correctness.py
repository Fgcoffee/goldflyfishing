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


# -- one or more (CR 603.2c) --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Whenever one or more other creatures die, draw a card.",
        "Whenever one or more creatures you control deal combat damage to a player, draw a card.",
        "Whenever one or more cards leave your graveyard, draw a card.",
        "Whenever you discard one or more cards, draw a card.",
        "Whenever you sacrifice one or more artifacts, draw a card.",
        "Whenever one or more +1/+1 counters are put on one or more creatures you control, draw a card.",
    ],
)
def test_one_or_more_is_batched(text):
    trigger = read(text)
    assert trigger.batched
    for spec in (trigger.subject, trigger.source):
        assert spec is None or spec.count is None


@pytest.mark.parametrize(
    "text",
    [
        "Whenever another creature dies, draw a card.",
        "Whenever a creature you control deals combat damage to a player, draw a card.",
        "Whenever one or more +1/+1 counters are put on a creature you control, draw a card.",
    ],
)
def test_a_single_subject_is_not_batched(text):
    assert not read(text).batched


@pytest.mark.parametrize(
    "text",
    [
        "Whenever two or more creatures attack, draw a card.",
        "Whenever one or more players discard one or more cards, draw a card.",
        "Whenever an opponent attacks one or more planeswalkers you control, draw a card.",
    ],
)
def test_unrepresentable_quantities_are_not_read(text):
    assert parse_trigger(Stream.of(text)) is None


def test_the_discarded_card_filter_is_kept():
    trigger = read("Whenever an opponent discards a creature card, draw a card.")
    assert trigger.subject is not None
    assert trigger.event_kinds == {EventKind.DISCARDED}
