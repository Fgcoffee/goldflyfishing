"""Edicts: "each opponent sacrifices a creature" (CR 701.21a, 608.2d).

The player named sacrifices, and chooses, from what they control. The
executor ignored who was named and took one creature from the whole board -
your own, as often as not - and only one however many opponents there were.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=3)


def alive(board, player: int) -> int:
    game = board.game
    return sum(
        1 for oid in game.battlefield
        if game.objects[oid].controller == PlayerId(player)
        and game.characteristics(game.objects[oid]).is_creature
    )


def cast(board, text: str) -> None:
    source = board.play("Sol Ring")
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        tuple(parse_effects(Stream.of(text))),
    )


def test_each_opponent_sacrifices_one_of_their_own(board):
    for player in (0, 0, 1, 1, 2, 2):
        board.play("Grizzly Bears", controller=player)
    cast(board, "Each opponent sacrifices a creature.")

    assert alive(board, 0) == 2
    assert alive(board, 1) == 1
    assert alive(board, 2) == 1


def test_an_opponent_with_nothing_sacrifices_nothing(board):
    board.play("Grizzly Bears", controller=0)
    board.play("Grizzly Bears", controller=2)
    cast(board, "Each opponent sacrifices a creature.")

    assert alive(board, 0) == 1
    assert alive(board, 2) == 0
