"""A modal header that changes as the modes are chosen (CR 700.2, 601.2b).

"Choose one. If this spell was kicked, choose any number instead." and
"Choose one. If you control a commander as you cast this spell, you may
choose both instead." are one header while the condition is false and
another while it is true, and the question is asked as the modes are chosen.
Kicker is announced in the same step (CR 601.2b), so a kicked spell has its
wider choice.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell, choose_modes
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Condition, ConditionKind, Value

DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")
GAIN = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2), text="gain 2 life")
SCRY = Effect(EffectKind.SCRY, players=YOU, amount=Value.of(1), text="scry 1")

KICKED = Condition(kind=ConditionKind.WAS_KICKED, text="this spell was kicked")


def inscription() -> Effect:
    """"Choose one. If this spell was kicked, choose any number instead." """
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=(DRAW, GAIN, SCRY),
        modes_instead=(KICKED, 3, True, 0),
        text="choose one",
    )


class Wants:
    def __init__(self, *modes: int) -> None:
        self.modes = modes
        self.budgets: list[int] = []

    def choose_modes(self, game, player, source, options, budget):
        self.budgets.append(budget)
        return self.modes


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _cast(board, *, kicked: bool):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import LETTER_TO_COLOR

    kicker = build(
        KeywordInstance(
            "Kicker",
            cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{1}")),)),
        )
    )
    board.scripts.add(
        "Shock", *kicker, Ability.spell(inscription(), text="choose one")
    )
    card = board.hand("Shock", controller=0)
    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(LETTER_TO_COLOR["R"]), 3)
    return cast_spell(
        board.game,
        PlayerId(0),
        Action(
            ActionKind.CAST_SPELL,
            source=card.id,
            additional_costs=(0,) if kicked else (),
        ),
    )


def test_a_kicked_inscription_may_choose_every_mode(board):
    agent = Wants(0, 1, 2)
    board.game.agents[0] = agent
    spell = _cast(board, kicked=True)
    assert agent.budgets == [3]
    assert spell.chosen_modes == (0, 1, 2)


def test_a_kicked_inscription_may_choose_none(board):
    """"Any number" includes zero, as Rankle's rulings say of the same words."""
    board.game.agents[0] = Wants()
    spell = _cast(board, kicked=True)
    assert spell.chosen_modes == ()


def test_an_unkicked_inscription_chooses_one(board):
    agent = Wants(0, 1, 2)
    board.game.agents[0] = agent
    spell = _cast(board, kicked=False)
    assert agent.budgets == [1]
    assert spell.chosen_modes == (0,)


def test_you_may_choose_both_instead_still_allows_one(board):
    """"You may choose both instead": one or both, never none."""
    modal = Effect(
        EffectKind.CHOOSE_MODE,
        children=(DRAW, GAIN),
        modes_instead=(Condition(kind=ConditionKind.ALWAYS), 2, True, 1),
        text="choose one",
    )
    board.game.agents[0] = Wants(1)
    assert choose_modes(board.game, None, (modal,), PlayerId(0)) == (1,)
    board.game.agents[0] = Wants(0, 1)
    assert choose_modes(board.game, None, (modal,), PlayerId(0)) == (0, 1)
    board.game.agents[0] = Wants()
    assert choose_modes(board.game, None, (modal,), PlayerId(0)) == (0,)
