"""Crew and saddle from their real oracle text, on a board (CR 702.122a,
702.171a).

Crew was built inert after the auditing session found it tapping N creatures
whatever their power. It is now the engine's total-power tap cost, on OTHER
untapped creatures you control - without "other", a crewed Vehicle could tap
itself to pay (auditor's F11).
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError, activate_ability
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)
CARAVAN = "Cultivator's Caravan"  # Crew 3; its other ability is a mana ability


@pytest.fixture
def board(card_db):
    board = make_board(card_db, OracleAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def crew_index(board, vehicle) -> int:
    abilities = board.game.characteristics(vehicle).abilities
    return next(i for i, a in enumerate(abilities) if a.keyword == "Crew")


def crew(board, vehicle):
    activate_ability(
        board.game,
        P0,
        Action(ActionKind.ACTIVATE_ABILITY, source=vehicle.id, ability_index=crew_index(board, vehicle)),
    )
    board.resolve_stack()


def test_crew_and_saddle_cards_are_read(card_db):
    for name in (CARAVAN, "Smuggler's Copter"):
        assert parse_card(card_db.lookup(name)).fully_parsed, name


def test_one_creature_with_enough_power_crews(board):
    vehicle = board.play(CARAVAN)
    giant = board.play("Hill Giant")
    crew(board, vehicle)
    assert giant.tapped
    assert board.chars(vehicle).is_creature


def test_two_creatures_adding_up_to_n_crew(board):
    """Bears (2) + Elves (1) is total power 3, which meets Crew 3."""
    vehicle = board.play(CARAVAN)
    bears = board.play("Grizzly Bears")
    elves = board.play("Llanowar Elves")
    crew(board, vehicle)
    assert bears.tapped and elves.tapped
    assert board.chars(vehicle).is_creature


def test_creatures_with_too_little_power_cannot_crew(board):
    vehicle = board.play(CARAVAN)
    thopters = [board.play("Ornithopter") for _ in range(3)]
    with pytest.raises(CastError):
        crew(board, vehicle)
    assert not any(t.tapped for t in thopters)
    assert not board.chars(vehicle).is_creature


def test_a_crewed_vehicle_cannot_tap_itself_to_crew(board):
    """F11: once crewed, the Caravan is a 5/5 creature, and "other" keeps it
    out of its own crew cost."""
    vehicle = board.play(CARAVAN)
    board.play("Hill Giant")
    crew(board, vehicle)
    with pytest.raises(CastError):
        crew(board, vehicle)
    assert not vehicle.tapped


def test_saddle_makes_the_mount_saddled(board, card_db):
    """CR 702.171a-b, from a real saddle card's oracle text."""
    from mtgfish.rules.kernel.conditions import holds
    from mtgfish.rules.kernel.query import Condition, ConditionKind

    mount = board.play("Ornery Tumblewagg")  # Saddle 2
    bears = board.play("Grizzly Bears")
    abilities = board.game.characteristics(mount).abilities
    index = next(i for i, a in enumerate(abilities) if a.keyword == "Saddle")
    activate_ability(
        board.game, P0, Action(ActionKind.ACTIVATE_ABILITY, source=mount.id, ability_index=index)
    )
    board.resolve_stack()
    assert bears.tapped
    assert holds(board.game, Condition(kind=ConditionKind.IS_SADDLED), source=mount.id, controller=P0)
