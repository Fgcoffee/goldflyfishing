"""Excess damage (CR 120.10).

Nothing in the engine could answer "was this permanent dealt excess damage",
so every ability that asks had nothing to read. The amount is measured before
the damage lands and travels on the damage event.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import deal_damage, excess_damage
from mtgfish.rules.kernel.events import EventKind


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _watch(board):
    """Collect the excess damage reported by each damage event."""
    seen: list[int] = []

    def observe(game, event):
        if event.kind in (EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT):
            seen.append(event.data[0] if event.data else 0)

    board.game.observer = observe
    return seen


# ---------------------------------------------------------------------------
# The measurement itself
# ---------------------------------------------------------------------------


def test_damage_short_of_lethal_is_not_excess(board):
    """CR 120.10: excess is the amount over lethal, and there is none here."""
    creature = board.play("Grizzly Bears", controller=0)  # 2/2
    assert excess_damage(board.game, creature, 1) == 0


def test_exactly_lethal_is_not_excess(board):
    """The boundary: 2 damage to a 2/2 is lethal, not excess."""
    creature = board.play("Grizzly Bears", controller=0)
    assert excess_damage(board.game, creature, 2) == 0


def test_damage_over_lethal_is_excess(board):
    """CR 120.10: 5 to a 2/2 is 3 excess."""
    creature = board.play("Grizzly Bears", controller=0)
    assert excess_damage(board.game, creature, 5) == 3


def test_damage_already_marked_makes_excess_come_sooner(board):
    """Lethal damage is what the creature can still take."""
    creature = board.play("Grizzly Bears", controller=0)
    creature.damage = 1
    assert excess_damage(board.game, creature, 3) == 2


def test_a_planeswalker_measures_against_its_loyalty(board):
    """CR 120.10: loyalty before the damage was dealt."""
    walker = board.play("Chandra, Torch of Defiance", controller=0)
    walker.add_counters("loyalty", 4)
    loyalty = walker.counter_count("loyalty")
    assert loyalty == 4
    assert excess_damage(board.game, walker, loyalty) == 0
    assert excess_damage(board.game, walker, loyalty + 2) == 2


def test_a_battle_measures_against_its_defense(board):
    """CR 120.10: defense before the damage was dealt."""
    battle = board.play("Invasion of Gobakhan", controller=0)
    battle.add_counters("defense", 3)
    defense = battle.counter_count("defense")
    assert defense == 3
    assert excess_damage(board.game, battle, defense + 1) == 1


def test_a_permanent_that_takes_no_damage_has_no_excess(board):
    """CR 120.3: damage to anything else does nothing, so nothing is excess."""
    land = board.play("Forest", controller=0)
    assert excess_damage(board.game, land, 5) == 0


# ---------------------------------------------------------------------------
# Reaching the event
# ---------------------------------------------------------------------------


def test_the_damage_event_carries_the_excess(board):
    """An ability that checks for excess damage reads it from the event."""
    creature = board.play("Grizzly Bears", controller=0)
    seen = _watch(board)
    deal_damage(board.game, creature, 5)
    assert seen[-1] == 3


def test_the_event_carries_zero_when_there_is_none(board):
    creature = board.play("Grizzly Bears", controller=0)
    seen = _watch(board)
    deal_damage(board.game, creature, 1)
    assert seen[-1] == 0


def test_two_sources_together_report_the_right_total(board):
    """CR 120.10 speaks of what sources dealt together. The engine deals them
    one at a time, so the excess arrives split - but it still adds up."""
    creature = board.play("Grizzly Bears", controller=0)  # 2/2
    seen = _watch(board)
    deal_damage(board.game, creature, 2)
    deal_damage(board.game, creature, 3)
    assert sum(seen) == 3
