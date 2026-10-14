"""Jump-start: cast from a graveyard for the mana cost plus a discard, then
exiled as it leaves the stack (CR 702.133a)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import (
    AlternativeCost,
    Cost,
    CostComponent,
    CostKind,
)
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import Value

P0 = PlayerId(0)

JUMP_START = Ability(
    AbilityKind.STATIC,
    keyword="Jump-start",
    alternative_cost=AlternativeCost(
        cost=Cost((CostComponent(CostKind.DISCARD, amount=Value.of(1)),)),
        from_zone=Zone.GRAVEYARD,
        pays_mana_cost=True,
        keyword="Jump-start",
        text="Jump-start",
    ),
    functions_in=frozenset({Zone.GRAVEYARD}),
    text="Jump-start",
)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities({"Radical Idea": (JUMP_START,)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    for oid in list(game.player(P0).hand):
        game.move_object(game.objects[oid], Zone.LIBRARY)
    return board


def casts_of(board, card):
    return [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def test_not_offered_with_an_empty_hand(board):
    board.game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 5)
    assert casts_of(board, board.graveyard("Radical Idea")) == []


def test_not_offered_without_the_mana_cost(board):
    board.hand("Island")
    assert casts_of(board, board.graveyard("Radical Idea")) == []


def test_pays_the_mana_cost_and_a_discard_then_is_exiled(board):
    game = board.game
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 2)
    board.hand("Island")
    board.hand("Forest")
    card = board.graveyard("Radical Idea")
    (action,) = casts_of(board, card)
    cast_spell(game, P0, action)
    # {1}{U} spent, one card discarded.
    assert game.player(P0).mana_pool.total == 0
    assert len(game.player(P0).hand) == 1
    board.resolve_stack()
    # The scripted card has no spell ability; only where it goes is asked.
    names = [game.objects[o].card.name for o in game.player(P0).graveyard]
    assert "Radical Idea" not in names
    assert any(
        o.is_live and o.zone is Zone.EXILE and o.card.name == "Radical Idea"
        for o in game.objects.values()
        if o.card is not None
    )
