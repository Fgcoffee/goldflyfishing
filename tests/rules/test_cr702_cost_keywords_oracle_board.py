"""Bloodthirst and spectacle from real oracle text, on a board.

Both were built wrong (auditor's F21): Stormblood Berserker's bloodthirst
never added counters, and spectacle was castable for its cost at any time.
The rules session's tests build each keyword by hand; these parse the card.
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.actions import deal_damage, lose_life
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, OracleAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def give(board, amount, color=Color.RED):
    board.game.player(P0).mana_pool.add(ManaKind(color), amount)


def test_the_cards_are_read(card_db):
    for name in ("Stormblood Berserker", "Skewer the Critics"):
        assert parse_card(card_db.lookup(name)).fully_parsed, name


def _cast_berserker(board):
    card = board.hand("Stormblood Berserker")
    give(board, 2)
    cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    board.settle()
    (berserker,) = [
        board.game.objects[i] for i in board.game.battlefield
        if board.game.printed_characteristics(board.game.objects[i]).name == "Stormblood Berserker"
    ]
    return berserker


def test_bloodthirst_after_an_opponent_was_dealt_damage(board):
    """CR 702.54a: Stormblood Berserker (1/1, bloodthirst 2) enters 3/3."""
    deal_damage(board.game, P1, 1, source_controller=P0)
    assert board.pt(_cast_berserker(board)) == (3, 3)


def test_no_bloodthirst_without_damage(board):
    assert board.pt(_cast_berserker(board)) == (1, 1)


def _spectacle_offered(board, card) -> bool:
    return any(
        a.kind is ActionKind.CAST_SPELL and a.source == card.id and a.alternative_cost >= 0
        for a in legal_actions(board.game, P0)
    )


def test_spectacle_only_after_an_opponent_lost_life(board):
    """CR 702.137a: Skewer the Critics ({2}{R}, spectacle {R})."""
    card = board.hand("Skewer the Critics")
    give(board, 1)
    assert not _spectacle_offered(board, card)
    lose_life(board.game, P1, 1)
    assert _spectacle_offered(board, card)
