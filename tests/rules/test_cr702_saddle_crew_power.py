"""Tapping creatures with total power N (crew CR 702.122a, saddle CR
702.171a), and the saddled designation (CR 702.171b-c)."""

from __future__ import annotations

import dataclasses

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError, activate_ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.enums import CardType, Phase, Step, Timing
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    Condition,
    ConditionKind,
    ControllerRelation,
    ObjectFilter,
    Value,
)

P0 = PlayerId(0)
OTHER_CREATURES = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU, other_than_source=True
)
SADDLED = Condition(kind=ConditionKind.IS_SADDLED)


def saddle(n: int) -> Ability:
    return Ability(
        AbilityKind.ACTIVATED,
        effects=(Effect(EffectKind.BECOME_SADDLED, text="becomes saddled"),),
        cost=Cost(
            (
                CostComponent(
                    CostKind.TAP_OTHER,
                    amount=Value.of(n),
                    filter=OTHER_CREATURES,
                    total_power=True,
                    text=f"tap creatures with total power {n}",
                ),
            )
        ),
        timing=Timing.SORCERY,
        keyword="Saddle",
        text=f"Saddle {n}",
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities({"Runeclaw Bear": (saddle(3),)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def activate(board, mount):
    activate_ability(
        board.game, P0, Action(ActionKind.ACTIVATE_ABILITY, source=mount.id, ability_index=0)
    )
    board.resolve_stack()


def test_one_creature_with_enough_power_pays_alone(board):
    """Total power, not a count: one 3/2 pays saddle 3."""
    mount = board.play("Runeclaw Bear")
    giant = board.play("Hill Giant")  # 3/3
    bears = board.play("Grizzly Bears")  # 2/2
    activate(board, mount)
    assert giant.tapped and not bears.tapped
    assert holds(board.game, SADDLED, source=mount.id, controller=P0)
    assert mount.saddled_by == (giant.id,)


def test_several_small_creatures_add_up(board):
    mount = board.play("Runeclaw Bear")
    first, second = board.play("Grizzly Bears"), board.play("Grizzly Bears")  # 2 + 2
    activate(board, mount)
    assert first.tapped and second.tapped
    assert holds(board.game, SADDLED, source=mount.id, controller=P0)


def test_not_enough_power_cannot_pay(board):
    """One 2/2 is 2 power, short of 3: the cost cannot be paid."""
    mount = board.play("Runeclaw Bear")
    board.play("Grizzly Bears")
    with pytest.raises(CastError):
        activate(board, mount)
    assert not holds(board.game, SADDLED, source=mount.id, controller=P0)


def test_saddled_ends_with_the_turn(board):
    mount = board.play("Runeclaw Bear")
    board.play("Hill Giant")
    activate(board, mount)
    board.game.turn += 1
    assert not holds(board.game, SADDLED, source=mount.id, controller=P0)


def test_the_mount_itself_cannot_pay(board):
    """"Other" creatures: a 3-power mount does not saddle itself."""
    mount = board.play("Hill Giant")
    board.scripts.add("Hill Giant", saddle(3))
    board.refresh()
    with pytest.raises(CastError):
        activate(board, mount)


def test_the_source_is_never_one_of_the_others_whatever_the_filter(board):
    """CR 702.122a: "other" is part of the cost itself. A crewed Vehicle is a
    creature, and a filter that forgets "another" must not let it pay."""
    from mtgfish.rules.kernel.legality import legal_actions

    loose = ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU)
    ability = saddle(3)
    component = ability.cost.components[0]
    ability = dataclasses.replace(
        ability,
        cost=Cost((dataclasses.replace(component, filter=loose),)),
    )
    mount = board.play("Hill Giant")
    board.scripts.add("Hill Giant", ability)
    board.refresh()
    assert not [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == mount.id
    ]
    with pytest.raises(CastError):
        activate(board, mount)
    assert not mount.tapped
