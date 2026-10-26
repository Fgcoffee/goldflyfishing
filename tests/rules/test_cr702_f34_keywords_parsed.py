"""Emerge, miracle, splice, bestow, embalm and eternalize from real cards.

The auditing session's F34: each was counted as read while its builder did
something else - Sacred Cat's embalm recast the card itself from the
graveyard, forever; Terminus offered its miracle cost from hand at any time.
Emerge is now built with its sacrifice (CR 702.119a); the other five are
inert placeholders until they can be built faithfully, which also takes
their costs off the table.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def give(board, amount, color):
    board.game.player(P0).mana_pool.add(ManaKind(color), amount)


def casts(board, name):
    game = board.game
    return [
        a for a in legal_actions(game, P0)
        if a.kind is ActionKind.CAST_SPELL
        and game.printed_characteristics(game.objects[a.source]).name == name
    ]


@pytest.mark.parametrize(
    "name", ["Sacred Cat", "Terminus", "Champion of Wits", "Boon Satyr", "Glacial Ray",
             "Desperate Ritual"],
)
def test_the_misbuilt_keyword_is_not_read(card_db, name):
    assert not parse_card(card_db.lookup(name)).fully_parsed


def test_embalm_does_not_recast_the_card_from_the_graveyard(board):
    board.game.move_object(board.hand("Sacred Cat"), Zone.GRAVEYARD, to_player=P0)
    give(board, 1, Color.WHITE)
    assert casts(board, "Sacred Cat") == []


def test_eternalize_does_not_recast_the_card_from_the_graveyard(board):
    board.game.move_object(board.hand("Champion of Wits"), Zone.GRAVEYARD, to_player=P0)
    give(board, 7, Color.BLUE)
    assert casts(board, "Champion of Wits") == []


def test_miracle_is_not_offered_from_hand_at_any_time(board):
    """Terminus ({4}{W}{W}, miracle {W}) with only {W}: not castable."""
    board.hand("Terminus")
    give(board, 1, Color.WHITE)
    assert casts(board, "Terminus") == []


def test_bestow_cost_is_not_offered(board):
    board.hand("Boon Satyr")
    give(board, 5, Color.GREEN)
    assert all(a.alternative_cost < 0 for a in casts(board, "Boon Satyr"))


def test_emerge_sacrifices_a_creature_and_is_reduced_by_its_mana_value(board):
    """Wretched Gryff, emerge {5}{U}: sacrificing Grizzly Bears (mana value 2)
    leaves {3}{U} to pay."""
    board.play("Grizzly Bears")
    board.hand("Wretched Gryff")
    give(board, 3, Color.BLUE)
    give(board, 1, Color.BLUE)
    emerged = [a for a in casts(board, "Wretched Gryff") if a.alternative_cost >= 0]
    assert emerged
    cast_spell(board.game, P0, emerged[0])
    assert "Grizzly Bears" in board.in_graveyard(0)
    assert board.game.player(P0).mana_pool.total == 0


def test_emerge_needs_a_creature_to_sacrifice(board):
    board.hand("Wretched Gryff")
    give(board, 6, Color.BLUE)
    assert not [a for a in casts(board, "Wretched Gryff") if a.alternative_cost >= 0]
