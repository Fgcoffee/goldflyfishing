"""Counters on a player (CR 122.1).

CR 122.1 puts a counter on "an object **or player**". The engine had four
named fields - poison, energy, experience, rad - and nowhere for anything
else, so a counter kind the rules had not given its own word could not sit on
a player at all. Ticket counters (CR 107.17a) are removed from the player, and
70 Commander-legal cards use them.

Both storages are reached through the same three methods, so a caller never
has to know which kind it is asking about.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def player(card_db):
    return make_board(card_db, ScriptedAbilities()).game.player(PlayerId(0))


def test_a_player_can_carry_an_arbitrary_counter(player):
    """CR 122.1, the half that was missing."""
    player.add_counters("ticket", 3)
    assert player.counter_count("ticket") == 3


def test_a_named_counter_still_answers_for_itself(player):
    """Poison has its own field because the rules name it constantly; asking
    by kind has to give the same answer as reading the field."""
    player.add_counters("poison", 2)
    assert player.counter_count("poison") == 2
    assert player.poison == 2


def test_removing_a_named_counter_moves_the_field(player):
    player.poison = 5
    assert player.remove_counters("poison", 2) == 2
    assert player.poison == 3


def test_removing_more_than_there_are_takes_what_there_is(player):
    """CR 122.1 has no negative counters: you cannot remove what is not there,
    and the count reports what actually came off."""
    player.add_counters("ticket", 2)
    assert player.remove_counters("ticket", 9) == 2
    assert player.counter_count("ticket") == 0


def test_removing_the_last_one_leaves_no_trace(player):
    player.add_counters("ticket", 1)
    player.remove_counters("ticket", 1)
    assert "ticket" not in player.counters


def test_adding_nothing_does_nothing(player):
    assert player.add_counters("ticket", 0) == 0
    assert player.counter_count("ticket") == 0


def test_an_unheld_counter_counts_zero(player):
    assert player.counter_count("never-heard-of-it") == 0


def test_kinds_do_not_bleed_into_each_other(player):
    player.add_counters("ticket", 2)
    player.add_counters("acorn", 1)
    assert (player.counter_count("ticket"), player.counter_count("acorn")) == (2, 1)
