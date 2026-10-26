"""Printed "protection from <quality>", read from the real cards, on a board.

Printed protection reached the engine with no quality, which is protection
from everything (auditor's F27): Lightning Bolt could not target White Knight
(protection from black), Giant Growth could not target its controller's own
Soltari Priest (protection from red), and Grizzly Bears could not block White
Knight. CR 702.16b-e asks only about sources with the stated quality.
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr500_turn_structure.cr506_combat import can_block
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    _candidates_for,
    targeting_effects,
)
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    return make_board(card_db, OracleAbilities())


def targetable(board, spell_name, target) -> bool:
    spell = board.hand(spell_name)
    (effect,) = targeting_effects(board.game, spell)
    return target.id in _candidates_for(board.game, spell, effect, P0)


def test_a_red_spell_can_target_protection_from_black(board):
    knight = board.play("White Knight", controller=1)
    assert targetable(board, "Lightning Bolt", knight)


def test_a_black_spell_cannot_target_protection_from_black(board):
    knight = board.play("White Knight", controller=1)
    assert not targetable(board, "Disfigure", knight)


def test_a_green_spell_can_target_protection_from_red(board):
    priest = board.play("Soltari Priest")
    assert targetable(board, "Giant Growth", priest)


def test_a_red_spell_cannot_target_protection_from_red(board):
    priest = board.play("Soltari Priest", controller=1)
    assert not targetable(board, "Lightning Bolt", priest)


def test_a_green_creature_can_block_protection_from_black(board):
    knight = board.play("White Knight", controller=1)
    bears = board.play("Grizzly Bears")
    assert can_block(board.game, bears, knight)


def test_a_black_creature_cannot_block_protection_from_black(board):
    knight = board.play("White Knight", controller=1)
    zombie = board.play("Walking Corpse")
    assert not can_block(board.game, zombie, knight)


def test_two_qualities_on_one_line(board):
    """Paladin en-Vec: protection from black and from red - both, and
    nothing else."""
    paladin = board.play("Paladin en-Vec", controller=1)
    assert not targetable(board, "Lightning Bolt", paladin)
    assert not targetable(board, "Disfigure", paladin)
    assert targetable(board, "Giant Growth", paladin)


def test_protection_from_a_creature_type(board):
    """Foothill Guide: protection from Goblins - a Goblin cannot block it,
    a Bear can."""
    guide = board.play("Foothill Guide", controller=1)
    assert not can_block(board.game, board.play("Goblin Piker"), guide)
    assert can_block(board.game, board.play("Grizzly Bears"), guide)
