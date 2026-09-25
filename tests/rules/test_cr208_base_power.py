"""Base power and toughness (CR 208.4b)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_emblem
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import (
    Comparison,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    Value,
)

P0 = PlayerId(0)
BASE_POWER_2 = ObjectFilter(base_power=NumericConstraint(Comparison.EQ, Value.of(2)))


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def anthem(board, kind: EffectKind, power: int, toughness: int) -> None:
    create_emblem(
        board.game,
        P0,
        (
            Ability.static(
                Effect(
                    kind,
                    targets=ObjectFilter(
                        types_all=CardType.CREATURE, controller=ControllerRelation.YOU
                    ),
                    amount=Value.of(power),
                    amount2=Value.of(toughness),
                )
            ),
        ),
    )
    board.refresh()


def test_base_power_is_the_printed_value(board):
    bears = board.play("Grizzly Bears")
    assert matches(board.game, bears, BASE_POWER_2)


def test_counters_and_pumps_do_not_change_base_power(board):
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 2)
    anthem(board, EffectKind.MODIFY_PT, 1, 1)
    chars = board.game.characteristics(bears)
    assert chars.power == 5
    assert chars.base_power == 2
    assert matches(board.game, bears, BASE_POWER_2)


def test_an_effect_that_sets_power_changes_base_power(board):
    bears = board.play("Grizzly Bears")
    anthem(board, EffectKind.SET_PT, 4, 4)
    assert board.game.characteristics(bears).base_power == 4
    assert not matches(board.game, bears, BASE_POWER_2)
