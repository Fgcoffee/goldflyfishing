"""X in a morph cost (CR 702.37f)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr100_game_concepts.cr116_special_actions import SpecialKind, perform
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    morph = build(
        KeywordInstance(
            "Morph",
            cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{X}{R}")),)),
        )
    )
    return make_board(card_db, ScriptedAbilities({"Grizzly Bears": morph}))


def turn_up(board, bears, x: int) -> bool:
    action = Action(
        ActionKind.SPECIAL,
        source=bears.id,
        ability_index=int(SpecialKind.TURN_FACE_UP),
        x_value=x,
    )
    return perform(board.game, P0, action)


def test_x_is_paid_and_remembered(board):
    bears = board.play("Grizzly Bears", face_down=True)
    pool = board.game.player(P0).mana_pool
    pool.add(ManaKind(Color.RED), 4)

    assert turn_up(board, bears, 3)
    assert not bears.face_down
    assert pool.total == 0
    assert bears.x_value == 3


def test_x_of_zero_costs_only_the_rest(board):
    """Control: X is a choice, and zero is one."""
    bears = board.play("Grizzly Bears", face_down=True)
    pool = board.game.player(P0).mana_pool
    pool.add(ManaKind(Color.RED), 1)

    assert turn_up(board, bears, 0)
    assert bears.x_value == 0
