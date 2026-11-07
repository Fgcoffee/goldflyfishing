"""Craft (CR 702.167a, 702.167b): exile this and the materials, from among
permanents you control and/or cards in your graveyard, and return this card
transformed. Spring-Loaded Sawblades // Bladewheel Chariot, "Craft with
artifact {3}{W}", with the keyword scripted as the parser will hand it over."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind, _perform
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import ObjectFilter

P0 = PlayerId(0)
CARD = "Spring-Loaded Sawblades"
CRAFT = KeywordInstance(
    "Craft",
    cost=Cost.mana("{3}{W}"),
    filter=ObjectFilter(types_all=CardType.ARTIFACT),
    amount=1,
    text="Craft with artifact {3}{W}",
)


@pytest.fixture
def board(card_db):
    for name in (CARD, "Sol Ring"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    board = make_board(card_db, ScriptedAbilities())
    full = card_db.lookup(CARD).name
    board.scripts.add(CARD, *build(CRAFT))
    board.scripts.add(full, *build(CRAFT))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.WHITE), 4)
    board.full_name = full
    return board


def craft_actions(board, blades):
    return [a for a in legal_actions(board.game, P0)
            if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == blades.id]


def live(board, name):
    return [o for o in board.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def craft(board, blades):
    (action,) = craft_actions(board, blades)
    assert _perform(board.game, P0, action)
    board.resolve_stack()
    (chariot,) = live(board, board.full_name)
    return chariot


def test_craft_with_a_graveyard_card_returns_it_transformed(board):
    blades = board.play(board.full_name)
    ring = board.graveyard("Sol Ring")
    chariot = craft(board, blades)
    assert chariot.zone is Zone.BATTLEFIELD
    assert chariot.face_index == 1, "it returns transformed (CR 712.14a)"
    assert board.game.characteristics(chariot).name == "Bladewheel Chariot"
    assert chariot.controller == P0
    (exiled_ring,) = live(board, "Sol Ring")
    assert exiled_ring.zone is Zone.EXILE and ring.superseded_by == exiled_ring.id


def test_a_permanent_you_control_can_be_the_material(board):
    blades = board.play(board.full_name)
    board.play("Sol Ring")
    craft(board, blades)
    (exiled_ring,) = live(board, "Sol Ring")
    assert exiled_ring.zone is Zone.EXILE


def test_the_crafting_permanent_is_not_its_own_material(board):
    """It is an artifact itself, but it pays "exile this" - with no other
    artifact around there is nothing to craft with."""
    blades = board.play(board.full_name)
    assert not craft_actions(board, blades)


def test_an_opponents_permanent_is_not_a_material(board):
    blades = board.play(board.full_name)
    board.play("Sol Ring", controller=1)
    assert not craft_actions(board, blades)
