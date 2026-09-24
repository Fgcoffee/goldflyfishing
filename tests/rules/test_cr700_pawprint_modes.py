"""Pawprint modes (CR 700.2i, 107.18).

A pawprint spell says "choose up to five {P} worth of modes", and each mode
prints one, two or three pawprints. That is not a second modal mechanism: it
is the ordinary one with a *budget* rather than a count, and with per-mode
weights that are not all 1. An ordinary bulleted mode and a single-pawprint
mode are the same thing, which is why "choose one" is a budget of one.

Three things separate it from CR 700.2's plain modal spell, and each is
tested here: the budget, the weights, and CR 700.2d's permission to choose
the same mode more than once - which every pawprint spell printed so far
grants in so many words.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    choose_modes,
    mode_budget,
    mode_weight,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Value

DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")
GAIN = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2), text="gain 2 life")
SCRY = Effect(EffectKind.SCRY, players=YOU, amount=Value.of(1), text="scry 1")


def pawprints(*weights: int, budget: int = 5, repeat: bool = True) -> Effect:
    """A "choose up to N {P} worth of modes" instruction."""
    modes = (DRAW, GAIN, SCRY)[: len(weights)]
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=modes,
        amount=Value.of(budget),
        mode_weights=weights,
        modes_may_repeat=repeat,
        modes_up_to=True,
        text=f"choose up to {budget} pawprints worth of modes",
    )


def choose_one(*modes: Effect) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE, children=modes, amount=Value.of(1), text="choose one"
    )


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


class Wants:
    def __init__(self, *modes: int) -> None:
        self.modes = modes
        self.budget: int | None = None
        self.weights: list[int] = []

    def choose_modes(self, game, player, source, options, budget):
        self.budget = budget
        self.weights = [weight for _, _, weight in options]
        return self.modes


def _chosen(board, modal, agent=None):
    if agent is not None:
        board.game.agents[0] = agent
    return choose_modes(board.game, None, (modal,), PlayerId(0))


# ---------------------------------------------------------------------------
# The weights themselves
# ---------------------------------------------------------------------------


def test_a_mode_costs_what_it_prints(board):
    """CR 700.2i: {P}{P} is worth two against the budget."""
    modal = pawprints(1, 2, 3)
    assert [mode_weight(modal, i) for i in range(3)] == [1, 2, 3]


def test_an_ordinary_mode_costs_one(board):
    """CR 700.2: a bulleted mode has no pawprints, so it weighs 1 - which is
    what makes this one mechanism rather than two."""
    modal = choose_one(DRAW, GAIN)
    assert [mode_weight(modal, i) for i in range(2)] == [1, 1]


def test_the_budget_is_what_the_instruction_says(board):
    assert mode_budget(pawprints(1, 2, 3, budget=5)) == 5
    assert mode_budget(choose_one(DRAW, GAIN)) == 1


# ---------------------------------------------------------------------------
# Spending the budget
# ---------------------------------------------------------------------------


def test_modes_may_be_chosen_up_to_the_budget(board):
    """1 + 2 = 3, inside a budget of 5."""
    agent = Wants(0, 1)
    assert _chosen(board, pawprints(1, 2, 3), agent) == (0, 1)


def test_the_whole_budget_may_be_spent(board):
    """2 + 3 = 5, exactly the budget."""
    agent = Wants(1, 2)
    assert _chosen(board, pawprints(1, 2, 3), agent) == (1, 2)


def test_a_choice_over_the_budget_is_refused(board):
    """CR 700.2i: the total may not be greater than the number specified. The
    third pick would take it to 6, so it is dropped and the rest stand."""
    agent = Wants(2, 2)  # 3 + 3 = 6
    assert _chosen(board, pawprints(1, 2, 3), agent) == (2,)


def test_the_same_mode_may_be_chosen_more_than_once(board):
    """CR 700.2d's exception, which every pawprint spell grants: 1+1+1 = 3."""
    agent = Wants(0, 0, 0)
    assert _chosen(board, pawprints(1, 2, 3), agent) == (0, 0, 0)


def test_a_repeat_is_refused_when_the_card_does_not_allow_it(board):
    """CR 700.2d's default: normally a mode cannot be chosen twice."""
    agent = Wants(0, 0, 1)
    assert _chosen(board, pawprints(1, 2, 3, repeat=False), agent) == (0, 1)


def test_the_agent_is_told_the_budget_and_the_weights(board):
    """An agent cannot choose sensibly without knowing what each mode costs."""
    agent = Wants(0)
    _chosen(board, pawprints(1, 2, 3), agent)
    assert agent.budget == 5
    assert agent.weights == [1, 2, 3]


# ---------------------------------------------------------------------------
# "Up to" versus "choose one"
# ---------------------------------------------------------------------------


def test_an_up_to_instruction_may_leave_the_budget_unspent(board):
    """CR 700.2i: the budget is a ceiling. One mode out of five worth is fine,
    and nothing tops it up."""
    agent = Wants(0)
    assert _chosen(board, pawprints(1, 2, 3), agent) == (0,)


def test_choose_one_is_not_optional(board):
    """CR 700.2 by contrast: a mode must be chosen where a legal one exists,
    so an agent that names none still gets one."""
    agent = Wants()
    assert _chosen(board, choose_one(DRAW, GAIN), agent) == (0,)


def test_choose_one_still_takes_exactly_one(board):
    """The control: a budget of 1 cannot buy two modes."""
    agent = Wants(0, 1)
    assert _chosen(board, choose_one(DRAW, GAIN), agent) == (0,)
