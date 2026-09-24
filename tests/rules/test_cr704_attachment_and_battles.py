"""Two state-based actions that were only half there (CR 704.5p, 704.5v)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr700_additional_rules.cr704_sba import check_state_based_actions
from mtgfish.rules.kernel.enums import Zone


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


# ---------------------------------------------------------------------------
# CR 704.5p
# ---------------------------------------------------------------------------


def test_a_creature_attached_to_something_falls_off(board):
    """CR 704.5p: a creature is never attached to anything, whatever put it
    there. It becomes unattached and stays on the battlefield."""
    host = board.play("Grizzly Bears", controller=0)
    creature = board.play("Runeclaw Bear", controller=0)
    creature.attached_to = host.id

    check_state_based_actions(board.game)

    assert not creature.attached_to
    assert creature.id in board.game.battlefield


def test_an_ordinary_permanent_attached_to_something_falls_off(board):
    """CR 704.5p: not an Aura, Equipment or Fortification, so it comes off."""
    host = board.play("Grizzly Bears", controller=0)
    land = board.play("Forest", controller=0)
    land.attached_to = host.id

    check_state_based_actions(board.game)

    assert not land.attached_to
    assert land.id in board.game.battlefield


def test_an_aura_is_not_merely_unattached(board):
    """CR 704.5m is the rule for Auras, and it is a graveyard, not a fall-off.
    CR 704.5p explicitly excludes them."""
    aura = board.play("Pacifism", controller=0)
    aura.attached_to = 0  # attached to nothing

    check_state_based_actions(board.game)

    assert aura.id not in board.game.battlefield


def test_an_equipment_on_a_legal_host_stays_put(board):
    """The control: CR 704.5n only unattaches an illegal attachment, and
    CR 704.5p does not reach Equipment at all."""
    host = board.play("Grizzly Bears", controller=0)
    equipment = board.play("Bonesplitter", controller=0)
    equipment.attached_to = host.id

    check_state_based_actions(board.game)

    assert equipment.attached_to == host.id


# ---------------------------------------------------------------------------
# CR 704.5v
# ---------------------------------------------------------------------------


def test_a_battle_at_zero_defense_goes_to_the_graveyard(board):
    """CR 704.5w, the ordinary case."""
    battle = board.play("Invasion of Gobakhan", controller=0)
    while battle.counter_count("defense"):
        battle.remove_counter("defense", battle.counter_count("defense"))

    check_state_based_actions(board.game)

    assert battle.id not in board.game.battlefield


def test_a_siege_stays_while_its_own_trigger_is_on_the_stack(board):
    """CR 704.5v: a Siege at 0 defense is spared while it is still the source
    of an ability that has triggered but not left the stack - otherwise the
    battle's own reward trigger would lose its source before resolving."""
    battle = board.play("Invasion of Gobakhan", controller=0)
    while battle.counter_count("defense"):
        battle.remove_counter("defense", battle.counter_count("defense"))

    trigger = board.play("Grizzly Bears", controller=0)
    board.game._remove_from_zone(trigger)
    trigger.zone = Zone.STACK
    trigger.source = battle.id
    board.game.stack.append(trigger.id)

    check_state_based_actions(board.game)

    assert battle.id in board.game.battlefield, "the Siege should have been spared"


def test_the_siege_goes_once_the_trigger_has_left(board):
    """And it is only a reprieve."""
    battle = board.play("Invasion of Gobakhan", controller=0)
    while battle.counter_count("defense"):
        battle.remove_counter("defense", battle.counter_count("defense"))

    check_state_based_actions(board.game)

    assert battle.id not in board.game.battlefield
