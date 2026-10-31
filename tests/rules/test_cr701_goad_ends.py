"""Goad lasts until the goading player's next turn (CR 701.15a)."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr500_turn import _untap_step
from mtgfish.rules.kernel.ids import PlayerId

P0, P1, P2 = PlayerId(0), PlayerId(1), PlayerId(2)


def test_goad_ends_as_the_goaders_next_turn_begins(card_db):
    board = make_board(card_db, ScriptedAbilities(), players=3)
    bears = board.play("Grizzly Bears", controller=0)
    bears.goaded_by.add(P1)
    board.game.active_player = P2
    _untap_step(board.game)
    assert bears.goaded_by == {P1}, "still goaded on someone else's turn"
    board.game.active_player = P1
    _untap_step(board.game)
    assert bears.goaded_by == set()


def test_each_goader_ends_only_its_own_goad(card_db):
    board = make_board(card_db, ScriptedAbilities(), players=3)
    bears = board.play("Grizzly Bears", controller=0)
    bears.goaded_by.update({P1, P2})
    board.game.active_player = P1
    _untap_step(board.game)
    assert bears.goaded_by == {P2}
