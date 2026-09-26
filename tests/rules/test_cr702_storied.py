"""Storied and the enduring story designation (CR 702.195)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import Condition, ConditionKind

P0 = PlayerId(0)
STORY = Condition(kind=ConditionKind.HAS_ENDURING_STORY)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Storied")))
    return board


def has_story(board) -> bool:
    return holds(board.game, STORY, source=0, controller=P0)


def test_three_artifacts_and_a_storied_permanent_give_a_story(board):
    board.play("Grizzly Bears")
    for _ in range(3):
        board.play("Sol Ring")
    board.sba()
    assert has_story(board)


def test_two_are_not_enough(board):
    board.play("Grizzly Bears")
    for _ in range(2):
        board.play("Sol Ring")
    board.sba()
    assert not has_story(board)


def test_without_a_storied_permanent_there_is_no_story(board):
    for _ in range(3):
        board.play("Sol Ring")
    board.sba()
    assert not has_story(board)


def test_the_story_lasts_the_rest_of_the_game(board):
    """CR 702.195a: "for the rest of the game"."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    bears = board.play("Grizzly Bears")
    rings = [board.play("Sol Ring") for _ in range(3)]
    board.sba()
    for obj in (bears, *rings):
        destroy(board.game, obj)
    board.sba()
    assert has_story(board)


def test_legendary_permanents_count(board):
    board.play("Grizzly Bears")
    for name in ("Isamaru, Hound of Konda", "Sol Ring", "Sol Ring"):
        board.play(name)
    board.sba()
    assert has_story(board)
