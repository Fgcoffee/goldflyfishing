"""Modal mana abilities (CR 605.3b, 700.2).

"{T}: Add {R} or {G}" is modal, and it is a mana ability, so it resolves
without ever becoming a stack object. Everything that reads the chosen modes
read them off the stack object, so the one kind of modal ability that has no
stack object chose its mode and then discarded it: the ability resolved as a
no-op and added no mana at all.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import TAP_COST
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability, cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value


class ModeAgent(FixedAgent):
    """An agent that asks for a particular mode."""

    def __init__(self, modes=(), **kwargs) -> None:
        super().__init__(**kwargs)
        self.modes = tuple(modes)

    def choose_modes(self, game, player, source, options, count):
        return self.modes


def add(symbol: str, color: Color) -> Effect:
    return Effect(
        EffectKind.ADD_MANA,
        colors=color,
        mana_produced=(symbol,),
        text=f"Add {symbol}",
    )


def choose_one(*modes: Effect) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=modes,
        amount=Value.of(1),
        text="choose one",
    )


def modal_mana_ability(*modes: Effect) -> Ability:
    return Ability(
        AbilityKind.ACTIVATED,
        effects=(choose_one(*modes),),
        cost=TAP_COST,
        is_mana_ability=True,
        text="{T}: Add {R} or {G}.",
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _tap(board, land):
    return activate_ability(
        board.game,
        PlayerId(0),
        Action(ActionKind.ACTIVATE_ABILITY, source=land.id, ability_index=0),
    )


def test_a_modal_mana_ability_adds_mana(board):
    """CR 605.3b: no stack object, so the modes cannot be read off one."""
    game = board.game
    board.scripts.add("Forest", modal_mana_ability(add("{R}", Color.RED), add("{G}", Color.GREEN)))
    land = board.play("Forest", controller=0)

    assert _tap(board, land) is None, "a mana ability must not use the stack"
    pool = game.player(PlayerId(0)).mana_pool
    assert pool.total == 1
    assert pool.amount_of(Color.RED) == 1


def test_the_controller_chooses_the_mana_mode(board):
    """CR 700.2b: the choice is the controller's, and it is made as the
    ability is activated."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add("Forest", modal_mana_ability(add("{R}", Color.RED), add("{G}", Color.GREEN)))
    land = board.play("Forest", controller=0)

    _tap(board, land)
    pool = game.player(PlayerId(0)).mana_pool
    assert pool.total == 1
    assert pool.amount_of(Color.GREEN) == 1


def test_only_the_chosen_mode_runs(board):
    """CR 700.2: the unchosen mode is not executed, so one mana, not two."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(0,))
    board.scripts.add("Forest", modal_mana_ability(add("{R}", Color.RED), add("{G}", Color.GREEN)))
    land = board.play("Forest", controller=0)

    _tap(board, land)
    pool = game.player(PlayerId(0)).mana_pool
    assert pool.amount_of(Color.GREEN) == 0
    assert pool.total == 1


def test_a_modal_mana_ability_tapped_to_pay_a_cost_adds_mana(board):
    """CR 601.2g: the engine activates mana abilities itself to pay a cost,
    and that activation chooses modes like any other (CR 602.2b)."""
    game = board.game
    board.scripts.add("Forest", modal_mana_ability(add("{G}", Color.GREEN), add("{R}", Color.RED)))
    board.play("Forest", controller=0)
    board.play("Forest", controller=0)
    card = board.hand("Grizzly Bears", controller=0)  # {1}{G}

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.stack, "the spell could not be paid for"


def test_a_modal_triggered_mana_ability_adds_mana(board):
    """CR 605.4: a triggered mana ability resolves without the stack too, so
    it has nowhere to keep its modes either."""
    game = board.game
    trigger = TriggerCondition(
        event_kinds=frozenset({EventKind.MANA_ADDED}),
        source=ObjectFilter(types_all=CardType.LAND),
        text="whenever a land you control produces mana",
    )
    board.scripts.add(
        "Sol Ring",
        Ability.triggered(
            trigger,
            choose_one(add("{W}", Color.WHITE), add("{U}", Color.BLUE)),
            text="add one mana of a colour",
        ),
    )
    board.play("Sol Ring", controller=0)
    board.scripts.add("Forest", modal_mana_ability(add("{G}", Color.GREEN)))
    land = board.play("Forest", controller=0)

    _tap(board, land)
    pool = game.player(PlayerId(0)).mana_pool
    assert pool.amount_of(Color.GREEN) == 1
    assert pool.amount_of(Color.WHITE) == 1
