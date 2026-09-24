"""Recruit (CR 701.70)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


class DiscardNamed:
    """Discards the card the test names, so the test decides what goes."""

    def __init__(self) -> None:
        self.name = ""

    def choose_discard(self, game, player):
        hand = game.player(player).hand
        return next(
            (i for i in hand if game.printed_characteristics(game.objects[i]).name == self.name),
            hand[0],
        )

    def choose_optional(self, game, player, effect):
        return True


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.agents[P0] = DiscardNamed()
    return board


def soldiers(board) -> list:
    game = board.game
    return [
        oid for oid in game.battlefield
        if "Soldier" in game.characteristics(game.objects[oid]).type_line.subtypes
    ]


def recruit(board) -> None:
    source = board.play("Grizzly Bears")
    execute(Resolution(game=board.game, source=source.id, controller=P0), build("Recruit"))


def test_discarding_a_nonland_card_makes_a_soldier(board):
    board.hand("Lightning Bolt")
    board.game.agents[P0].name = "Lightning Bolt"
    recruit(board)
    (token,) = soldiers(board)
    chars = board.game.characteristics(board.game.objects[token])
    assert (chars.power, chars.toughness) == (1, 1)


def test_discarding_a_land_makes_nothing(board):
    board.hand("Forest")
    board.game.agents[P0].name = "Forest"
    recruit(board)
    assert not soldiers(board)
