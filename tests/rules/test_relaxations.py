"""Rules an instrument may switch off (CR 704.5a-c, 704.5b, 500.4).

The sandbox is a bench, not a game, and the rules that end games end bench
sessions before they show anything: a hand-built board has no library, so the
first draw step decks you, and a life total set to 1 to watch a drain effect
ends the session instead of demonstrating the effect.

What is tested here is that the suspensions are *opt-in and complete*: a game
built without them behaves exactly as before, and a game built with them does
not merely delay the loss to the next check.
"""

from __future__ import annotations

import random

import pytest

from mtgfish.rules.cr100_game_concepts.player import Player
from mtgfish.rules.cr500_turn_structure.cr500_turn import _empty_mana_pools
from mtgfish.rules.cr700_additional_rules.cr704_sba import check_state_based_actions
from mtgfish.rules.kernel.enums import LossReason, Phase, Step
from mtgfish.rules.kernel.game import Game
from mtgfish.rules.kernel.ids import ObjectId, PlayerId
from mtgfish.rules.kernel.log import GameLog
from mtgfish.rules.kernel.relaxations import BENCH, NAMES, STRICT, Relaxations


def board(relaxations: Relaxations = STRICT) -> Game:
    """Two seats and nothing else - no cards, and therefore no library."""
    game = Game(rng=random.Random(0), log=GameLog(enabled=True))
    for index, name in enumerate(("You", "Opponent")):
        game.players.append(Player(id=PlayerId(index), name=name))
    game.turn_order = [PlayerId(0), PlayerId(1)]
    game.active_player = PlayerId(0)
    game.priority_player = PlayerId(0)
    game.turn = 1
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.relaxations = relaxations
    return game


# ---------------------------------------------------------------------------
# A game is strict unless told otherwise
# ---------------------------------------------------------------------------


def test_a_game_enforces_every_rule_by_default():
    """The default has to be the real rules, or a run could inherit a bench."""
    assert Game().relaxations is STRICT
    assert not STRICT.any_active
    assert all(value is False for value in STRICT.as_dict().values())


def test_every_relaxation_is_explained():
    """A switch nobody can explain is a switch nobody should flip."""
    from mtgfish.rules.kernel.relaxations import DESCRIPTIONS

    assert set(DESCRIPTIONS) == set(NAMES)
    assert all(DESCRIPTIONS[name].strip() for name in NAMES)


def test_an_unknown_relaxation_is_refused():
    """The caller is a UI sending strings; a typo must not silently do nothing."""
    with pytest.raises(KeyError):
        STRICT.with_field("players_cannot_win_either", True)


# ---------------------------------------------------------------------------
# CR 704.5a-c, 903.10: the four ways a player loses
# ---------------------------------------------------------------------------


def test_zero_life_still_loses_the_game_normally():
    game = board()
    game.player(PlayerId(0)).life = 0
    check_state_based_actions(game)
    assert game.player(PlayerId(0)).has_lost
    assert game.player(PlayerId(0)).loss_reason is LossReason.LIFE
    assert game.game_over


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda p: setattr(p, "life", -7), id="life"),
        pytest.param(lambda p: setattr(p, "poison", 12), id="poison"),
        pytest.param(
            lambda p: setattr(p, "attempted_draw_from_empty_library", True),
            id="empty-library",
        ),
        pytest.param(
            lambda p: p.take_commander_damage(ObjectId(99), 21),
            id="commander-damage",
        ),
    ],
)
def test_no_player_loses_on_a_bench(setup):
    """All four, together. A bench that saves you from one still ends."""
    game = board(BENCH)
    setup(game.player(PlayerId(0)))
    check_state_based_actions(game)
    assert not game.player(PlayerId(0)).has_lost
    assert not game.game_over


def test_a_spared_player_does_not_leave_work_for_every_later_check():
    """The reason the check returns early rather than sparing each loser.

    A loser who is collected and then spared is still a *pending* loser: the
    fixed-point loop finds work on every pass and spins to its iteration cap
    at every priority check, for the rest of the session.
    """
    game = board(BENCH)
    game.player(PlayerId(0)).life = -7
    assert check_state_based_actions(game) is False, "nothing to do, so nothing done"
    assert check_state_based_actions(game) is False


def test_the_board_still_says_what_happened():
    """Not losing is not the same as not being hit. The life total is the
    evidence that the effect under test worked."""
    game = board(BENCH)
    game.player(PlayerId(0)).life = -7
    check_state_based_actions(game)
    assert game.player(PlayerId(0)).life == -7


# ---------------------------------------------------------------------------
# CR 704.5b: drawing from an empty library
# ---------------------------------------------------------------------------


def test_drawing_from_an_empty_library_normally_marks_the_player():
    game = board()
    assert game.draw(PlayerId(0), 1) == []
    assert game.player(PlayerId(0)).attempted_draw_from_empty_library


def test_drawing_from_an_empty_library_on_a_bench_does_nothing():
    """The one that made the sandbox useless: a board assembled by hand has no
    library, so the first draw step killed whoever was driving it."""
    game = board(BENCH)
    assert game.draw(PlayerId(0), 3) == []
    assert not game.player(PlayerId(0)).attempted_draw_from_empty_library

    check_state_based_actions(game)
    assert not game.player(PlayerId(0)).has_lost


# ---------------------------------------------------------------------------
# CR 500.4: mana pools empty at the end of every step and phase
# ---------------------------------------------------------------------------


def test_mana_pools_normally_empty_at_the_end_of_a_step():
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    game = board()
    game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.RED), 3)
    _empty_mana_pools(game)
    assert game.player(PlayerId(0)).mana_pool.total == 0


def test_mana_pools_can_be_made_to_persist():
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    game = board(BENCH)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.RED), 3)
    _empty_mana_pools(game)
    assert game.player(PlayerId(0)).mana_pool.total == 3


# ---------------------------------------------------------------------------
# Switching one back on
# ---------------------------------------------------------------------------


def test_a_rule_put_back_is_enforced_again():
    """The rule itself is sometimes the thing being tested."""
    game = board(BENCH.with_field("players_cannot_lose", False))
    game.player(PlayerId(0)).life = 0
    check_state_based_actions(game)
    assert game.player(PlayerId(0)).has_lost
    # And the others are untouched by putting that one back.
    assert game.relaxations.draws_from_an_empty_library_do_nothing
