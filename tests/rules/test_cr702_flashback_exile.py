"""A flashed-back spell is exiled as it leaves the stack (CR 702.34a, and
the same rule for jump-start 702.133a, aftermath 702.127a, harmonize
702.180a)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    flashback = build(
        KeywordInstance(
            "Flashback",
            cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{1}{U}")),)),
        )
    )
    board = make_board(card_db, ScriptedAbilities({"Divination": flashback}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 3)
    return board


def zones_of_divination(board) -> list[Zone]:
    game = board.game
    return [
        o.zone
        for o in game.objects.values()
        if o.is_live and o.card is not None and o.card.name == "Divination"
    ]


def flash_back(board):
    card = board.graveyard("Divination")
    (action,) = [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]
    return cast_spell(board.game, P0, action)


def test_a_flashed_back_spell_is_exiled_as_it_resolves(board):
    flash_back(board)
    board.resolve_stack()
    assert zones_of_divination(board) == [Zone.EXILE]


def test_a_countered_flashback_spell_is_exiled_too(board):
    """"Anywhere else": the graveyard a countered spell would go to."""
    spell = flash_back(board)
    board.game.move_object(spell, Zone.GRAVEYARD)
    assert zones_of_divination(board) == [Zone.EXILE]


def test_cast_from_hand_it_goes_to_the_graveyard(board):
    """Control: the exile belongs to the flashback cost, not to the card."""
    card = board.hand("Divination")
    cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    assert zones_of_divination(board) == [Zone.GRAVEYARD]
