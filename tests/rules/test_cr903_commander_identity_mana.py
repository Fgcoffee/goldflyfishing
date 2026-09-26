"""Mana "of any color in your commander's color identity" (CR 903.4, 903.4f)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.compile import parse_face
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.effects = parse_face(card_db.lookup("Command Tower"), 0).abilities[0].effects
    return board


def tap_the_tower(board) -> dict:
    tower = board.play("Command Tower")
    pool = board.game.player(P0).mana_pool
    colours = (Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN)
    before = {c: pool.amount_of(c) for c in colours}
    execute(Resolution(game=board.game, source=tower.id, controller=P0), board.effects)
    return {c: pool.amount_of(c) - n for c, n in before.items() if pool.amount_of(c) != n}


def make_commander(board, name: str) -> None:
    commander = board.play(name)
    commander.is_commander = True
    board.game.player(P0).commanders = (commander.id,)


def test_the_tower_makes_a_colour_of_the_commanders_identity(board):
    make_commander(board, "Llanowar Elves")  # mono-green identity
    assert tap_the_tower(board) == {Color.GREEN: 1}


def test_without_a_commander_the_tower_makes_nothing(board):
    """CR 903.4f: the quality is undefined, so that part does nothing."""
    board.game.player(P0).commanders = ()
    assert tap_the_tower(board) == {}
