"""Modal spells and abilities (CR 700.2, 603.3c, 602.2b).

A modal spell or ability is one with a bulleted list of options and an
instruction to choose among them. The choice is made when the object is put on
the stack, not when it resolves - so the resolver can only run what was
already chosen. Spells announced their modes (CR 601.2b) and everything else
announced nothing, which made every modal triggered and activated ability an
elaborate no-op.

The modes here are hand-built rather than parsed from a card: what is under
test is the rule, and a card's wording is a separate question.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import FREE
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    activate_ability,
    cast_spell,
    legal_modes,
)
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import put_triggers_on_stack
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Phase, Step
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, ObjectFilter, Value

CREATURES = ObjectFilter(types_all=CardType.CREATURE)
ARTIFACTS = ObjectFilter(types_all=CardType.ARTIFACT)

DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")
GAIN = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2), text="you gain 2 life")
SCRY = Effect(EffectKind.SCRY, players=YOU, amount=Value.of(1), text="scry 1")
KILL_CREATURE = Effect(
    EffectKind.DESTROY, targets=CREATURES, is_targeted=True, text="destroy target creature"
)
KILL_ARTIFACT = Effect(
    EffectKind.DESTROY, targets=ARTIFACTS, is_targeted=True, text="destroy target artifact"
)


def choose_one(*modes: Effect, count: int = 1) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=modes,
        amount=Value.of(count),
        text="choose one",
    )


def modal_trigger(*modes: Effect, count: int = 1) -> Ability:
    """"Whenever a creature dies, choose one - ..." """
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.DIES}),
            subject=CREATURES,
            text="whenever a creature dies",
        ),
        choose_one(*modes, count=count),
        text="Whenever a creature dies, choose one.",
    )


class ModeAgent(FixedAgent):
    """An agent that asks for particular modes, legal or not."""

    def __init__(self, modes=(), **kwargs) -> None:
        super().__init__(**kwargs)
        self.modes = tuple(modes)
        self.offered: list[list[int]] = []
        self.counts: list[int] = []

    def choose_modes(self, game, player, source, options, count):
        self.offered.append([index for index, _ in options])
        self.counts.append(count)
        return self.modes


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _fodder(board):
    """A creature to kill, so the dies-trigger has something to fire on."""
    return board.play("Llanowar Elves", controller=0)


# ---------------------------------------------------------------------------
# Triggered abilities (CR 603.3c)
# ---------------------------------------------------------------------------


def test_a_modal_trigger_chooses_a_mode_without_an_agent_hook(board):
    """CR 700.2b: a mode is chosen when the ability goes on the stack."""
    game = board.game
    board.scripts.add("Blood Artist", modal_trigger(DRAW, GAIN))
    board.play("Blood Artist", controller=0)
    hand = game.player(PlayerId(0)).hand_size

    destroy(game, _fodder(board))
    board.settle()
    assert game.stack, "the modal trigger never reached the stack"
    assert game.objects[game.stack[-1]].chosen_modes == (0,)

    board.resolve_stack()
    assert game.player(PlayerId(0)).hand_size == hand + 1


def test_the_controller_chooses_which_mode(board):
    game = board.game
    agent = ModeAgent(modes=(1,))
    game.agents[0] = agent
    board.scripts.add("Blood Artist", modal_trigger(DRAW, GAIN))
    board.play("Blood Artist", controller=0)
    hand = game.player(PlayerId(0)).hand_size
    life = game.player(PlayerId(0)).life

    destroy(game, _fodder(board))
    board.settle()
    board.resolve_stack()

    assert agent.offered == [[0, 1]]
    assert agent.counts == [1]
    assert game.player(PlayerId(0)).life == life + 2
    assert game.player(PlayerId(0)).hand_size == hand


def test_a_mode_with_no_legal_target_cannot_be_chosen(board):
    """CR 700.2b: an illegal mode is off the menu, however hard it is asked for."""
    game = board.game
    agent = ModeAgent(modes=(0,))
    game.agents[0] = agent
    board.scripts.add("Blood Artist", modal_trigger(KILL_ARTIFACT, GAIN))
    board.play("Blood Artist", controller=0)
    life = game.player(PlayerId(0)).life

    destroy(game, _fodder(board))
    board.settle()
    assert agent.offered == [[1]], "an unchoosable mode was offered"
    assert game.objects[game.stack[-1]].chosen_modes == (1,)

    board.resolve_stack()
    assert game.player(PlayerId(0)).life == life + 2


def test_a_trigger_with_no_legal_mode_is_removed_from_the_stack(board):
    """CR 603.3c: with no mode to choose, the ability never stays on the stack."""
    game = board.game
    board.scripts.add("Blood Artist", modal_trigger(KILL_ARTIFACT, KILL_ARTIFACT))
    board.play("Blood Artist", controller=0)

    destroy(game, _fodder(board))
    assert put_triggers_on_stack(game) == 0
    assert not game.stack


def test_targets_belong_to_the_chosen_mode_only(board):
    """CR 700.2c: an unchosen mode's targets are not chosen at all."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add("Blood Artist", modal_trigger(KILL_CREATURE, KILL_ARTIFACT))
    board.play("Blood Artist", controller=0)
    board.play("Grizzly Bears", controller=0)
    ring = board.play("Sol Ring", controller=0)

    destroy(game, _fodder(board))
    board.settle()
    stack_object = game.objects[game.stack[-1]]
    assert stack_object.chosen_modes == (1,)
    assert stack_object.targets == ((ring.id,),)

    board.resolve_stack()
    assert "Sol Ring" in board.in_graveyard(0)
    assert "Grizzly Bears" in board.alive(0)


def test_two_modes_are_two_different_modes(board):
    """CR 700.2d: the same mode is not chosen twice."""
    game = board.game
    agent = ModeAgent(modes=(2, 2))
    game.agents[0] = agent
    board.scripts.add("Blood Artist", modal_trigger(DRAW, SCRY, GAIN, count=2))
    board.play("Blood Artist", controller=0)
    hand = game.player(PlayerId(0)).hand_size
    life = game.player(PlayerId(0)).life

    destroy(game, _fodder(board))
    board.settle()
    assert agent.counts == [2]
    assert game.objects[game.stack[-1]].chosen_modes == (2, 0)

    board.resolve_stack()
    assert game.player(PlayerId(0)).life == life + 2
    assert game.player(PlayerId(0)).hand_size == hand + 1


def test_a_mode_is_judged_by_its_filters_when_the_source_has_gone(board):
    """CR 603.3d: a trigger outlives its source, and its modes are still weighed.

    With nothing left to check protection against, the filters alone decide
    which modes are on the menu.
    """
    game = board.game
    board.play("Sol Ring", controller=0)

    modal = choose_one(KILL_ARTIFACT, KILL_CREATURE)
    assert legal_modes(game, None, modal, PlayerId(0)) == [0]


# ---------------------------------------------------------------------------
# Activated abilities (CR 602.2b -> 601.2b)
# ---------------------------------------------------------------------------


def test_a_modal_activated_ability_chooses_its_mode(board):
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add(
        "Sol Ring",
        Ability.activated(FREE, choose_one(DRAW, GAIN), text="choose one"),
    )
    ring = board.play("Sol Ring", controller=0)
    life = game.player(PlayerId(0)).life

    stack_object = activate_ability(
        game, PlayerId(0), Action(ActionKind.ACTIVATE_ABILITY, source=ring.id, ability_index=0)
    )
    assert stack_object is not None
    assert stack_object.chosen_modes == (1,)

    board.resolve_stack()
    assert game.player(PlayerId(0)).life == life + 2


def test_a_modal_activated_ability_targets_the_chosen_mode(board):
    game = board.game
    game.agents[0] = ModeAgent(modes=(1,))
    board.scripts.add(
        "Sol Ring",
        Ability.activated(FREE, choose_one(KILL_CREATURE, KILL_ARTIFACT), text="choose one"),
    )
    ring = board.play("Sol Ring", controller=0)
    board.play("Grizzly Bears", controller=0)

    stack_object = activate_ability(
        game, PlayerId(0), Action(ActionKind.ACTIVATE_ABILITY, source=ring.id, ability_index=0)
    )
    assert stack_object.targets == ((ring.id,),)

    board.resolve_stack()
    assert "Sol Ring" in board.in_graveyard(0)
    assert "Grizzly Bears" in board.alive(0)


# ---------------------------------------------------------------------------
# Spells (CR 601.2b)
# ---------------------------------------------------------------------------


def test_a_modal_spell_is_asked_when_the_action_names_no_modes(board):
    game = board.game
    agent = ModeAgent(modes=(1,))
    game.agents[0] = agent
    board.scripts.add("Shock", Ability.spell(choose_one(DRAW, GAIN), text="choose one"))
    card = board.hand("Shock", controller=0)
    _give_mana(board, 0, 3)
    life = game.player(PlayerId(0)).life

    spell = cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert spell.chosen_modes == (1,)

    board.resolve_stack()
    assert game.player(PlayerId(0)).life == life + 2


def test_a_modal_spell_keeps_the_modes_the_action_named(board):
    """CR 601.2b: an announced choice is not second-guessed."""
    game = board.game
    game.agents[0] = ModeAgent(modes=(0,))
    board.scripts.add("Shock", Ability.spell(choose_one(DRAW, GAIN), text="choose one"))
    card = board.hand("Shock", controller=0)
    _give_mana(board, 0, 3)
    life = game.player(PlayerId(0)).life

    spell = cast_spell(
        game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id, mode_choices=(1,))
    )
    assert spell.chosen_modes == (1,)

    board.resolve_stack()
    assert game.player(PlayerId(0)).life == life + 2


def _give_mana(board, player: int, amount: int, color: str = "R") -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import LETTER_TO_COLOR

    board.game.player(PlayerId(player)).mana_pool.add(ManaKind(LETTER_TO_COLOR[color]), amount)

