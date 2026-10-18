"""Aftermath: the second half is cast from a graveyard and only from there,
and exiled as it leaves the stack when it was (CR 702.127a)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)

AFTERMATH = Ability(
    AbilityKind.STATIC,
    keyword="Aftermath",
    functions_in=frozenset({Zone.HAND, Zone.GRAVEYARD, Zone.STACK}),
    text="Aftermath",
)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities({"Dawn": (AFTERMATH,)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.WHITE), 10)
    return board


def casts_of(board, card):
    return [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def zones_of(board) -> list[Zone]:
    return [
        o.zone
        for o in board.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == "Dusk // Dawn"
    ]


def test_from_hand_only_the_first_half_is_offered(board):
    card = board.hand("Dusk // Dawn")
    assert {a.face_index for a in casts_of(board, card)} == {0}


def test_from_a_graveyard_only_the_aftermath_half_is_offered(board):
    card = board.graveyard("Dusk // Dawn")
    actions = casts_of(board, card)
    assert [a.face_index for a in actions] == [1]


def test_the_aftermath_half_is_exiled_as_it_resolves(board):
    card = board.graveyard("Dusk // Dawn")
    (action,) = casts_of(board, card)
    spell = cast_spell(board.game, P0, action)
    assert spell.cast_from is Zone.GRAVEYARD
    board.resolve_stack()
    assert zones_of(board) == [Zone.EXILE]
    # It was paid for with its own mana cost, {3}{W}{W}.
    assert board.game.player(P0).mana_pool.total == 5


def test_the_first_half_cast_from_hand_goes_to_the_graveyard(board):
    card = board.hand("Dusk // Dawn")
    (action,) = casts_of(board, card)
    cast_spell(board.game, P0, action)
    board.resolve_stack()
    assert zones_of(board) == [Zone.GRAVEYARD]
