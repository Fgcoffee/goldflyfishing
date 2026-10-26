"""Attacking the player with the most life (CR 702.105a, dethrone)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import _combat
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import Condition, ConditionKind

P0, P1, P2 = PlayerId(0), PlayerId(1), PlayerId(2)
MOST_LIFE = Condition(kind=ConditionKind.ATTACKING_PLAYER_WITH_MOST_LIFE)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=3)


def attacking(board, defender: PlayerId):
    bears = board.play("Grizzly Bears", controller=0)
    _combat(board.game).attacking[bears.id] = defender
    return bears


def test_attacking_the_player_with_the_most_life(board):
    board.game.player(P2).life = 30
    bears = attacking(board, P1)
    assert holds(board.game, MOST_LIFE, source=bears.id, controller=P0)


def test_a_tie_for_the_most_counts(board):
    bears = attacking(board, P1)  # everyone is on 40
    assert holds(board.game, MOST_LIFE, source=bears.id, controller=P0)


def test_attacking_someone_with_less_does_not(board):
    board.game.player(P1).life = 30
    bears = attacking(board, P1)
    assert not holds(board.game, MOST_LIFE, source=bears.id, controller=P0)


def test_the_attackers_own_life_counts_too(board):
    """"The player with the most life" includes the attacking player."""
    board.game.player(P0).life = 50
    bears = attacking(board, P1)
    assert not holds(board.game, MOST_LIFE, source=bears.id, controller=P0)


def test_not_attacking_is_not_attacking_anyone(board):
    bears = board.play("Grizzly Bears", controller=0)
    assert not holds(board.game, MOST_LIFE, source=bears.id, controller=P0)
