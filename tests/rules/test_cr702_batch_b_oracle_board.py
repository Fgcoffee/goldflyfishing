"""Squad, offspring and conspire from their real oracle text, on a board.

All three were counted as read while their costs were paid for nothing
(auditor's F21). They now build the linked token-copy trigger (squad,
offspring) and the tap-two-sharing-a-color cost (conspire), on the rules
session's REPEAT_PER_PAYMENT, TokenSpec.copy_of and keyword-linked copy.
"""

from __future__ import annotations

import pytest
from harness import make_board
from test_cost_keywords_board_b import additional_index, cast, give, named, run

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, OracleAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def test_the_cards_are_read(card_db):
    for name in ("Agate Instigator", "Powder Ganger", "Burn Trail"):
        assert parse_card(card_db.lookup(name)).fully_parsed, name


def test_offspring_paid_makes_a_1_1_token_copy(board):
    """Agate Instigator with offspring paid: a 1/1 token copy of it."""
    card = board.hand("Agate Instigator")
    give(board, 6, Color.RED)
    cast(board, card, additional=(additional_index(board, card, "Offspring"),))
    run(board)
    copies = named(board, "Agate Instigator")
    assert len(copies) == 2
    (token,) = [c for c in copies if c.is_token]
    assert board.pt(token) == (1, 1)


def test_offspring_unpaid_makes_none(board):
    card = board.hand("Agate Instigator")
    give(board, 6, Color.RED)
    cast(board, card)
    run(board)
    assert len(named(board, "Agate Instigator")) == 1


def test_squad_paid_twice_makes_two_token_copies(board):
    card = board.hand("Powder Ganger")
    give(board, 10, Color.RED)
    index = additional_index(board, card, "Squad")
    cast(board, card, additional=(index, index))
    run(board)
    gangers = named(board, "Powder Ganger")
    assert len(gangers) == 3
    assert sum(1 for g in gangers if g.is_token) == 2


def test_conspire_copies_burn_trail(board):
    """Burn Trail (3 damage to any target), conspired with two red creatures:
    the spell and its copy both resolve, so a 6/6 takes 6 and dies."""
    board.play("Raging Goblin")
    board.play("Goblin Piker")
    dreadmaw = board.play("Colossal Dreadmaw", controller=1)
    card = board.hand("Burn Trail")
    give(board, 4, Color.RED)
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell

    cast_spell(
        board.game,
        P0,
        Action(
            ActionKind.CAST_SPELL,
            source=card.id,
            additional_costs=(additional_index(board, card, "Conspire"),),
            targets=((dreadmaw.id,),),
        ),
    )
    run(board)
    board.sba()
    assert "Colossal Dreadmaw" not in board.alive(1)
