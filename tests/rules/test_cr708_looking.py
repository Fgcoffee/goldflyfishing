"""Who may look at a face-down object (CR 708.5, 708.6)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr700_additional_rules.cr708_face_down import (
    characteristics_seen_by,
    face_down_order,
    may_look_at,
)
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def test_the_controller_may_look_and_an_opponent_may_not(board):
    hidden = board.play("Hill Giant", controller=0, face_down=True)
    assert may_look_at(board.game, P0, hidden)
    assert not may_look_at(board.game, P1, hidden)
    assert characteristics_seen_by(board.game, P0, hidden).name == "Hill Giant"
    seen_by_opponent = characteristics_seen_by(board.game, P1, hidden)
    assert seen_by_opponent.name == ""
    assert (seen_by_opponent.power, seen_by_opponent.toughness) == (2, 2)


def test_nobody_may_look_at_a_face_down_card_in_exile(board):
    card = board.hand("Hill Giant")
    exiled = board.game.move_object(card, Zone.EXILE, face_down="")
    assert not may_look_at(board.game, P0, exiled)


def test_face_up_objects_hide_nothing(board):
    bears = board.play("Grizzly Bears", controller=1)
    assert may_look_at(board.game, P0, bears)


def test_face_down_permanents_are_told_apart_by_arrival(board):
    first = board.play("Hill Giant", controller=0, face_down=True)
    second = board.play("Grizzly Bears", controller=0, face_down=True)
    assert [o.id for o in face_down_order(board.game, P0)] == [first.id, second.id]
