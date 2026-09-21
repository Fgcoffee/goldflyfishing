"""Changing the targets of a modal ability on the stack (CR 115.7, 700.2c).

A modal object has one target slot per targeting effect *of the modes it
chose* - that is what CR 700.2c means by an unchosen mode's targets never
being chosen. Redirection walked every mode's targeting effects instead, so it
lined the existing targets up against the wrong filters and rebuilt more slots
than the object has.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import FREE
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute_one
from mtgfish.rules.kernel.enums import CardType, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value

CREATURES = ObjectFilter(types_all=CardType.CREATURE)
ARTIFACTS = ObjectFilter(types_all=CardType.ARTIFACT)
ON_THE_STACK = ObjectFilter(zones=frozenset({Zone.STACK}))

KILL_CREATURE = Effect(
    EffectKind.DESTROY, targets=CREATURES, is_targeted=True, text="destroy target creature"
)
KILL_ARTIFACT = Effect(
    EffectKind.DESTROY, targets=ARTIFACTS, is_targeted=True, text="destroy target artifact"
)
REDIRECT = Effect(
    EffectKind.CHANGE_TARGETS,
    targets=ON_THE_STACK,
    is_targeted=True,
    text="change the target of target ability",
)


class ModeAgent(FixedAgent):
    def __init__(self, modes=(), **kwargs) -> None:
        super().__init__(**kwargs)
        self.modes = tuple(modes)

    def choose_modes(self, game, player, source, options, count):
        return self.modes


def choose_one(*modes: Effect) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE, children=modes, amount=Value.of(1), text="choose one"
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _redirect(board, stack_object):
    execute_one(
        Resolution(
            game=board.game,
            source=stack_object.id,
            controller=PlayerId(0),
            targets=((stack_object.id,),),
        ),
        REDIRECT,
    )


def test_a_modal_ability_keeps_one_target_slot_per_chosen_mode(board):
    """CR 700.2c: only the chosen mode has targets, so redirection rebuilds
    exactly one slot, not one per mode."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add(
        "Sol Ring",
        Ability.activated(FREE, choose_one(KILL_CREATURE, KILL_ARTIFACT), text="choose one"),
    )
    ring = board.play("Sol Ring", controller=0)
    other = board.play("Mind Stone", controller=0)
    bears = board.play("Grizzly Bears", controller=0)

    ability = activate_ability(
        game, PlayerId(0), Action(ActionKind.ACTIVATE_ABILITY, source=ring.id, ability_index=0)
    )
    assert ability.chosen_modes == (1,)
    assert ability.targets == ((ring.id,),)

    _redirect(board, ability)

    assert len(ability.targets) == 1, "a slot was rebuilt for an unchosen mode"
    # CR 115.3: the new target is a different legal one - an artifact, because
    # that is what the chosen mode asks for.
    assert ability.targets == ((other.id,),)
    assert bears.id not in ability.targets[0]


def test_the_redirected_ability_still_resolves_onto_its_new_target(board):
    """The slots line up with execution order, so what was rebuilt is what
    the resolver reaches (CR 601.2c)."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add(
        "Sol Ring",
        Ability.activated(FREE, choose_one(KILL_CREATURE, KILL_ARTIFACT), text="choose one"),
    )
    ring = board.play("Sol Ring", controller=0)
    board.play("Mind Stone", controller=0)
    board.play("Grizzly Bears", controller=0)

    ability = activate_ability(
        game, PlayerId(0), Action(ActionKind.ACTIVATE_ABILITY, source=ring.id, ability_index=0)
    )
    _redirect(board, ability)
    board.resolve_stack()

    assert "Mind Stone" in board.in_graveyard(0)
    assert "Grizzly Bears" in board.alive(0)
