"""An untargeted effect that names a number affects that many (CR 608.2).

"Put a +1/+1 counter on a creature you control" and "put a +1/+1 counter on
each creature you control" are different sentences. The filter says which by
carrying a count - and the count was read only when the effect *targeted*, so
every untargeted one acted on the whole matching set.

Bolster is the case that shows it: it chooses one creature and it was putting
counters on all of them.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter, Value

MINE = {"types_all": CardType.CREATURE, "controller": ControllerRelation.YOU}


def counters_on(effect_filter, board, amount=1):
    execute(
        Resolution(game=board.game, source=0, controller=PlayerId(0)),
        (
            Effect(
                EffectKind.ADD_COUNTERS,
                targets=effect_filter,
                counter_type="+1/+1",
                amount=Value.of(amount),
                text="put a +1/+1 counter",
            ),
        ),
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.creatures = [board.play("Grizzly Bears", controller=0) for _ in range(3)]
    return board


def _counted(board):
    return [c.counter_count("+1/+1") for c in board.creatures]


def test_a_count_of_one_affects_one(board):
    """The bug: this put a counter on all three."""
    counters_on(ObjectFilter(**MINE, count=Value.of(1)), board)
    assert sorted(_counted(board)) == [0, 0, 1]


def test_a_count_of_two_affects_two(board):
    counters_on(ObjectFilter(**MINE, count=Value.of(2)), board)
    assert sorted(_counted(board)) == [0, 1, 1]


def test_no_count_still_means_every_match(board):
    """The control, and what a mass effect needs: "each creature you control"
    carries no count and must keep reaching all of them."""
    counters_on(ObjectFilter(**MINE), board)
    assert _counted(board) == [1, 1, 1]


def test_a_count_larger_than_the_board_affects_everything(board):
    """Asking for five when three exist is three, not an error."""
    counters_on(ObjectFilter(**MINE, count=Value.of(5)), board)
    assert _counted(board) == [1, 1, 1]


def test_up_to_zero_affects_nothing(board):
    """CR 107.1b: "up to" a count that came out zero is no objects - which is
    the opposite of what an unread count did."""
    counters_on(ObjectFilter(**MINE, count=Value.of(0), up_to=True), board)
    assert _counted(board) == [0, 0, 0]


def test_the_choice_is_deterministic(card_db):
    """With no agent to ask, a replay still has to reproduce."""
    runs = []
    for _ in range(2):
        board = make_board(card_db, ScriptedAbilities())
        board.creatures = [board.play("Grizzly Bears", controller=0) for _ in range(3)]
        counters_on(ObjectFilter(**MINE, count=Value.of(1)), board)
        runs.append(_counted(board))
    assert runs[0] == runs[1]


def test_an_opponents_creature_is_never_chosen(board):
    """The control that keeps the narrowing from widening: the filter still
    filters before the count picks."""
    theirs = board.play("Runeclaw Bear", controller=1)
    counters_on(ObjectFilter(**MINE, count=Value.of(3)), board)
    assert theirs.counter_count("+1/+1") == 0


def test_rest_in_peace_exiles_every_graveyard(card_db, tmp_path):
    """Rest in Peace's "exile all cards from all graveyards", read by the real
    parser, exiles every card in every graveyard. Its filter carried a bare
    int as its count, and reading that as a Value aborted the resolution."""
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules.kernel.enums import Zone
    from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

    card_db.registry()
    for name in ("Rest in Peace", "Grizzly Bears", "Shock"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    box.game.agents[0] = PassiveOpponent()
    box.game.agents[1] = PassiveOpponent()
    box.put("Grizzly Bears", "graveyard", 0)
    box.put("Shock", "graveyard", 1)
    box.put("Rest in Peace", "battlefield", 0)
    box.settle()
    for _ in range(5):
        if not box.game.stack:
            break
        box.resolve_top()

    def where(name):
        return [
            obj.zone
            for obj in box.game.objects.values()
            if obj.is_live and obj.card is not None and obj.card.name == name
        ]

    assert where("Grizzly Bears") == [Zone.EXILE]
    assert where("Shock") == [Zone.EXILE]
    assert not box.game.player(PlayerId(0)).graveyard
    assert not box.game.player(PlayerId(1)).graveyard
