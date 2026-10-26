"""A floor under an up-to mode budget (CR 700.2).

"Choose one or both" and "choose one or more" let the player stop short of
the ceiling but not at zero; "choose any number" and "choose up to one" let
them choose nothing. The budget is the ceiling (CR 700.2i's "up to"), and
``modes_at_least`` is the floor.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import choose_modes
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Value

DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")
GAIN = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2), text="gain 2 life")
SCRY = Effect(EffectKind.SCRY, players=YOU, amount=Value.of(1), text="scry 1")


def header(*, budget: int, at_least: int) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=(DRAW, GAIN, SCRY),
        amount=Value.of(budget),
        modes_up_to=True,
        modes_at_least=at_least,
        text="choose one or more",
    )


class Wants:
    def __init__(self, *modes: int) -> None:
        self.modes = modes

    def choose_modes(self, game, player, source, options, budget):
        return self.modes


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _chosen(board, modal, agent=None):
    if agent is not None:
        board.game.agents[0] = agent
    return choose_modes(board.game, None, (modal,), PlayerId(0))


def test_one_or_more_may_take_every_mode(board):
    assert _chosen(board, header(budget=3, at_least=1), Wants(0, 1, 2)) == (0, 1, 2)


def test_one_or_more_may_stop_at_one(board):
    assert _chosen(board, header(budget=3, at_least=1), Wants(2)) == (2,)


def test_one_or_more_cannot_be_none(board):
    """An empty answer is topped up to the floor, and only to the floor."""
    assert _chosen(board, header(budget=3, at_least=1), Wants()) == (0,)


def test_one_or_more_without_an_agent_chooses_one(board):
    assert _chosen(board, header(budget=3, at_least=1)) == (0,)


def test_any_number_may_be_none(board):
    """Rankle's ruling: "you can choose zero modes"."""
    assert _chosen(board, header(budget=3, at_least=0), Wants()) == ()


def test_the_ceiling_still_holds(board):
    assert _chosen(board, header(budget=2, at_least=1), Wants(0, 1, 2)) == (0, 1)
