"""Facing a villainous choice (CR 701.55)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    EACH_OPPONENT,
    YOU,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
)

P0 = PlayerId(0)
THAT_PLAYER = PlayerFilter(PlayerScope.THAT_PLAYER)

SACRIFICE = Effect(
    EffectKind.SACRIFICE,
    players=THAT_PLAYER,
    targets=ObjectFilter(types_all=CardType.CREATURE, count=Value.of(1)),
    text="that player sacrifices a creature",
)
LOSE_LIFE = Effect(
    EffectKind.LOSE_LIFE, players=THAT_PLAYER, amount=Value.of(4), text="that player loses 4 life"
)
DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="you draw a card")


class Picks:
    def __init__(self, choices: dict) -> None:
        self.choices = choices
        self.asked: list = []

    def choose_villainous_option(self, game, player, options):
        self.asked.append(player)
        return self.choices.get(player, 0)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=3)


def face(board, *options) -> None:
    source = board.play("Sol Ring")
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        (Effect(EffectKind.VILLAINOUS_CHOICE, players=EACH_OPPONENT, children=options),),
    )


def creatures(board, player: int) -> int:
    game = board.game
    return sum(
        1 for oid in game.battlefield
        if game.objects[oid].controller == PlayerId(player)
        and game.characteristics(game.objects[oid]).is_creature
    )


def test_each_facing_player_chooses_and_that_player_means_them(board):
    game = board.game
    for player in (1, 2):
        board.play("Grizzly Bears", controller=player)
    agents = {1: Picks({PlayerId(1): 0}), 2: Picks({PlayerId(2): 1})}
    for player, agent in agents.items():
        game.agents[PlayerId(player)] = agent
    life = game.player(PlayerId(2)).life

    face(board, SACRIFICE, LOSE_LIFE)

    assert creatures(board, 1) == 0  # player 1 chose the sacrifice
    assert creatures(board, 2) == 1  # player 2 chose the life loss
    assert game.player(PlayerId(2)).life == life - 4
    assert game.player(PlayerId(1)).life == life


def test_an_option_that_is_impossible_may_still_be_chosen(board):
    """CR 701.55b: nothing to sacrifice, so the choice costs nothing."""
    game = board.game
    game.agents[PlayerId(1)] = Picks({PlayerId(1): 0})
    library = len(game.player(P0).library)
    face(board, SACRIFICE, DRAW)
    assert len(game.player(P0).library) == library  # no card drawn for them


def test_players_face_it_in_apnap_order(board):
    """CR 701.55d."""
    game = board.game
    game.active_player = PlayerId(2)
    order: list = []

    class Record:
        def choose_villainous_option(self, g, player, options):
            order.append(int(player))
            return 1

    for player in (1, 2):
        game.agents[PlayerId(player)] = Record()
    face(board, SACRIFICE, DRAW)
    assert order == [2, 1]
