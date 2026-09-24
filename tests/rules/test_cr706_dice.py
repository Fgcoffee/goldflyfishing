"""Rolling dice (CR 706).

The engine could roll from its seeded RNG and nothing could ask it to: there
was no effect kind for a die roll, so the AFR d20 cycle and everything like it
had no way to exist.

CR 706.3b is why this is one opcode rather than several. The dice, the
modifiers, the results table and whatever reads the result afterwards "are all
part of one ability", so splitting them would let an ability half-happen.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import (
    DiceOutcome,
    Effect,
    EffectKind,
)
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Value, ValueKind


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def resolution(board, source=0):
    return Resolution(game=board.game, source=source, controller=PlayerId(0))


def roll(sides=6, count=1, **kw):
    return Effect(
        EffectKind.ROLL_DICE,
        amount=Value.of(count),
        dice_sides=sides,
        text=f"roll {count} d{sides}",
        **kw,
    )


GAIN_1 = Effect(
    EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(1), text="you gain 1 life"
)
GAIN_10 = Effect(
    EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(10), text="you gain 10 life"
)
GAIN_ROLL = Effect(
    EffectKind.GAIN_LIFE,
    players=YOU,
    amount=Value(kind=ValueKind.DIE_ROLL_RESULT),
    text="you gain life equal to the result",
)


# ---------------------------------------------------------------------------
# The roll itself
# ---------------------------------------------------------------------------


def test_a_roll_produces_a_result_in_range(board):
    """CR 706.1: the die has the number of faces the effect says."""
    res = resolution(board)
    execute(res, (roll(sides=20),))
    assert len(res.die_results) == 1
    assert 1 <= res.die_results[0] <= 20


def test_rolling_several_dice_produces_several_results(board):
    res = resolution(board)
    execute(res, (roll(sides=6, count=3),))
    assert len(res.die_results) == 3


def test_a_die_with_no_faces_rolls_nothing(board):
    """A malformed effect does nothing rather than inventing a number."""
    res = resolution(board)
    execute(res, (roll(sides=0),))
    assert res.die_results == ()


def test_the_roll_is_reproducible_from_the_seed(card_db):
    """Every choice draws from the game's seeded RNG, because a replay
    reconstructs a game from its seed alone."""
    runs = []
    for _ in range(2):
        board = make_board(card_db, ScriptedAbilities())
        res = resolution(board)
        execute(res, (roll(sides=20, count=5),))
        runs.append(res.die_results)
    assert runs[0] == runs[1]


# ---------------------------------------------------------------------------
# CR 706.2, modifiers
# ---------------------------------------------------------------------------


def test_a_modifier_is_added_to_the_natural_result(board):
    """CR 706.2: the natural result plus the modifiers is the result."""
    res = resolution(board)
    execute(res, (roll(sides=1, count=1, dice_modifier=3),))
    assert res.die_results == (4,)  # a d1 always rolls 1


def test_a_negative_modifier_subtracts(board):
    res = resolution(board)
    execute(res, (roll(sides=1, count=1, dice_modifier=-1),))
    assert res.die_results == (0,)


# ---------------------------------------------------------------------------
# CR 706.3a, the results table
# ---------------------------------------------------------------------------


def test_the_matching_striation_happens(board):
    """CR 706.3a: the result picks which listed effect happens."""
    life = board.game.player(PlayerId(0)).life
    res = resolution(board)
    execute(
        res,
        (
            roll(
                sides=1,
                outcomes=(
                    DiceOutcome(1, 1, (GAIN_1,)),
                    DiceOutcome(2, 2, (GAIN_10,)),
                ),
            ),
        ),
    )
    assert board.game.player(PlayerId(0)).life == life + 1


def test_only_the_first_matching_striation_happens(board):
    """A table is a list of alternatives, not a set of filters: one result
    picks one effect."""
    life = board.game.player(PlayerId(0)).life
    res = resolution(board)
    execute(
        res,
        (roll(sides=1, outcomes=(DiceOutcome(1, 1, (GAIN_1,)), DiceOutcome(1, 1, (GAIN_10,)))),),
    )
    assert board.game.player(PlayerId(0)).life == life + 1


def test_a_result_outside_every_striation_does_nothing(board):
    """CR 706.3a: "determine which effect happens, *if any*"."""
    life = board.game.player(PlayerId(0)).life
    res = resolution(board)
    execute(res, (roll(sides=1, outcomes=(DiceOutcome(5, 6, (GAIN_10,)),)),))
    assert board.game.player(PlayerId(0)).life == life


def test_an_open_ended_striation_has_no_upper_bound(board):
    """CR 706.3a's "N+" form."""
    assert DiceOutcome(4).covers(4)
    assert DiceOutcome(4).covers(999)
    assert not DiceOutcome(4).covers(3)


def test_each_die_consults_the_table(board):
    """CR 706.3a applies per result, so two dice run the table twice."""
    life = board.game.player(PlayerId(0)).life
    res = resolution(board)
    execute(res, (roll(sides=1, count=2, outcomes=(DiceOutcome(1, 1, (GAIN_1,)),)),))
    assert board.game.player(PlayerId(0)).life == life + 2


# ---------------------------------------------------------------------------
# CR 706.4, no table
# ---------------------------------------------------------------------------


def test_the_result_is_readable_by_what_follows(board):
    """CR 706.4: no table, so the ability says what to do with the number."""
    life = board.game.player(PlayerId(0)).life
    res = resolution(board)
    execute(res, (roll(sides=1, count=3), GAIN_ROLL))
    assert board.game.player(PlayerId(0)).life == life + 3


# ---------------------------------------------------------------------------
# CR 706.6, ignoring a roll
# ---------------------------------------------------------------------------


def test_ignoring_the_lowest_drops_it(board):
    """CR 706.6: an ignored roll is considered never to have happened, so it
    is gone before anything reads the results."""
    res = resolution(board)
    execute(res, (roll(sides=20, count=3, dice_ignore_lowest=1),))
    assert len(res.die_results) == 2


def test_the_ignored_roll_is_the_lowest_one(board):
    res = resolution(board)
    execute(res, (roll(sides=20, count=4, dice_ignore_lowest=2),))
    assert len(res.die_results) == 2
    assert res.die_results == tuple(sorted(res.die_results))


def test_ignoring_nothing_keeps_every_roll(board):
    """The control."""
    res = resolution(board)
    execute(res, (roll(sides=20, count=3),))
    assert len(res.die_results) == 3
