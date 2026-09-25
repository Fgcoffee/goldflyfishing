"""Foretelling costs {2} (CR 116.2f, 702.143a)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr116_special_actions import (
    SpecialKind,
    available,
    perform,
)
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities({"Divination": (keyword("Foretell"),)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def foretell(board):
    (action,) = [a for a in available(board.game, P0) if a.kind is SpecialKind.FORETELL]
    return action.as_action()


def test_foretelling_spends_two_mana(board):
    card = board.hand("Divination")
    pool = board.game.player(P0).mana_pool
    pool.add(ManaKind(Color.BLUE), 3)

    assert perform(board.game, P0, foretell(board))
    assert pool.total == 1
    exiled = board.game.objects[card.id].superseded_by
    assert board.game.objects[exiled].zone is Zone.EXILE
    assert board.game.objects[exiled].face_down


def test_foretell_is_not_offered_without_the_mana(board):
    board.hand("Divination")
    assert not [a for a in available(board.game, P0) if a.kind is SpecialKind.FORETELL]
