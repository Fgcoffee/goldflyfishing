"""The condition grammar says exactly how a quantity is compared, and of whom.

Each of these was read wrongly before, and parsed cleanly while doing it:

* "you have seven or more cards in hand" was a life total;
* "one or fewer cards" was "1 or more";
* "you control two or fewer other lands" (the fastland cycle) was "at least 2";
* "an opponent controls three or more creatures" dropped the count;
* "a player has 13 or less life" required every player to;
* "you cast a noncreature spell this turn" dropped "noncreature".
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_condition_text
from mtgfish.parser.tokens import Stream
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.query import (
    Comparison,
    ConditionKind,
    PlayerScope,
    ValueKind,
)


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def read(text):
    stream = Stream.of(text)
    condition = parse_condition_text(stream)
    assert condition is not None and stream.done, f"unread: {stream.remaining()}"
    return condition


def unread(text):
    stream = Stream.of(text)
    condition = parse_condition_text(stream)
    return condition is None or not stream.done


# -- hand size and life --------------------------------------------------------


def test_cards_in_hand_is_a_hand_size_not_a_life_total():
    condition = read("you have seven or more cards in hand")
    assert condition.kind is ConditionKind.CARDS_IN_HAND
    assert condition.constraint.comparison is Comparison.GE
    assert condition.constraint.value.constant == 7


def test_or_fewer_is_an_upper_bound():
    condition = read("you have one or fewer cards in your hand")
    assert condition.kind is ConditionKind.CARDS_IN_HAND
    assert condition.constraint.comparison is Comparison.LE
    assert condition.constraint.value.constant == 1


def test_no_cards_in_hand_is_exactly_zero():
    condition = read("an opponent has no cards in hand")
    assert condition.players.scope is PlayerScope.OPPONENT
    assert condition.constraint.comparison is Comparison.EQ
    assert condition.constraint.value.constant == 0


def test_a_player_is_you_or_an_opponent():
    condition = read("a player has 13 or less life")
    assert condition.kind is ConditionKind.OR
    scopes = {operand.players.scope for operand in condition.operands}
    assert scopes == {PlayerScope.YOU, PlayerScope.OPPONENT}


def test_a_bare_life_total_is_not_read_as_a_threshold():
    assert unread("you have 20 life")


def test_gained_life_this_turn_is_history_not_a_life_total():
    condition = read("you've gained 3 or more life this turn")
    assert condition.kind is ConditionKind.EVENT_THIS_TURN
    assert condition.event_kinds == (int(EventKind.LIFE_GAINED),)
    assert condition.constraint.value.constant == 3


# -- counts of permanents ----------------------------------------------------------


def test_two_or_fewer_other_lands_is_at_most_two():
    condition = read("you control two or fewer other lands")
    assert condition.kind is ConditionKind.CONTROLS_MATCHING
    assert condition.constraint.comparison is Comparison.LE
    assert condition.constraint.value.constant == 2
    assert condition.filter.other_than_source


def test_another_is_one_other_than_this():
    condition = read("you control another Goblin")
    assert condition.constraint.comparison is Comparison.GE
    assert condition.constraint.value.constant == 1
    assert condition.filter.other_than_source


def test_an_opponent_controlling_several_is_asked_per_opponent():
    condition = read("an opponent controls three or more creatures")
    assert condition.kind is ConditionKind.COMPARE_COUNTS
    assert condition.players.scope is PlayerScope.OPPONENT
    assert condition.constraint.value.constant == 3


def test_a_bare_count_above_one_is_declined():
    assert unread("you control three artifacts")


def test_there_are_keeps_its_bound():
    condition = read("there are seven or more cards in your graveyard")
    assert condition.constraint.comparison is Comparison.GE
    assert condition.constraint.value.constant == 7


# -- this turn ---------------------------------------------------------------------


def test_raid_is_having_attacked():
    condition = read("you attacked this turn")
    assert condition.kind is ConditionKind.EVENT_THIS_TURN
    assert condition.event_kinds == (int(EventKind.ATTACKS),)
    assert condition.constraint is None


@pytest.mark.parametrize(
    "text",
    [
        "you cast a noncreature spell this turn",
        "you sacrificed three or more Clues this turn",
        "an opponent was dealt 3 or more damage this turn",
    ],
)
def test_a_qualified_noun_the_tally_cannot_see_is_declined(text):
    assert unread(text)


# -- one object -------------------------------------------------------------------


def test_a_test_about_it_is_asked_of_it():
    condition = read("that creature is attacking")
    assert condition.kind is ConditionKind.REMEMBERED_MATCHES
    assert condition.filter.attacking


def test_several_creatures_attacking_is_not_one_object():
    assert unread("three or more creatures are attacking")


# -- values -------------------------------------------------------------------------


def test_devotion_is_compared_as_a_value():
    condition = read("your devotion to red is less than five")
    assert condition.kind is ConditionKind.VALUE_COMPARE
    assert condition.value.kind is ValueKind.DEVOTION
    assert condition.constraint.comparison is Comparison.LT


def test_total_power_is_compared_as_a_value():
    condition = read("creatures you control have total power 10 or greater")
    assert condition.kind is ConditionKind.VALUE_COMPARE
    assert condition.value.kind is ValueKind.TOTAL_AMONG
