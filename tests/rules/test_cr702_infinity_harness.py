"""Harness (CR 701.64) and ∞ (CR 702.186)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build as build_action
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Value

P0 = PlayerId(0)

UPKEEP_DRAW = Ability.triggered(
    TriggerCondition(
        event_kinds=frozenset({EventKind.UPKEEP}), players=YOU, text="at the beginning of your upkeep"
    ),
    Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),
    text="∞ - At the beginning of your upkeep, draw a card.",
)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.scripts.add("Sol Ring", *build(KeywordInstance("∞", abilities=(UPKEEP_DRAW,))))
    return board


def harness(board, obj) -> None:
    execute(Resolution(game=board.game, source=obj.id, controller=P0), build_action("Harness"))


def upkeep_triggers(board) -> int:
    board.game.pending_triggers.clear()
    board.game.emit(Event(EventKind.UPKEEP, player=P0))
    return len(board.game.pending_triggers)


def test_harnessing_marks_the_permanent_and_taps_nothing(board):
    """CR 701.64a: a designation, not a tap."""
    stone = board.play("Sol Ring")
    harness(board, stone)
    assert stone.harnessed
    assert not stone.tapped


def test_the_infinity_ability_works_only_while_harnessed(board):
    stone = board.play("Sol Ring")
    assert upkeep_triggers(board) == 0
    harness(board, stone)
    assert upkeep_triggers(board) == 1


def test_leaving_the_battlefield_ends_harnessed(board):
    """CR 701.64b: until it leaves the battlefield."""
    stone = board.play("Sol Ring")
    harness(board, stone)
    back = board.game.move_object(stone, Zone.HAND)
    assert not back.harnessed
