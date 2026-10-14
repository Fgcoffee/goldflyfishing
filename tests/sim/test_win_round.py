"""What a person means by "won on turn six".

The engine counts player-turns: every player's turn advances one counter, so a
four-player game's fourth round is turn thirteen. The results screen showed
that counter and called it the win turn, which is a number about four times
larger than anything anybody means.

Dividing it by the number of seats is not the fix either, and this is the part
that is easy to get wrong: players who are eliminated stop taking turns, so the
division drifts from the truth the moment anybody dies. In a measured game it
read 17.8 where the real answer was 22.1. So the number is counted, not
derived.
"""

from __future__ import annotations

import pytest

from mtgfish.sim.observer import Observer, new_record
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.sim.stats import Report


def began(observer, game, player: int) -> None:
    observer(game, Event(EventKind.TURN_BEGAN, player=PlayerId(player)))


class FakeGame:
    """Enough of a game for the observer's turn counting."""

    turn = 0
    players: list = []
    winners: tuple = ()

    def player(self, pid):  # pragma: no cover - not reached by these tests
        raise AssertionError("these tests never look at a player")


# ---------------------------------------------------------------------------
# Counting a player's own turns
# ---------------------------------------------------------------------------


def test_a_seat_counts_only_its_own_turns():
    observer = Observer(new_record(0, 0, 4), hero=0)
    game = FakeGame()
    for turn in range(12):          # four seats, three rounds each
        began(observer, game, turn % 4)

    assert observer.turns_begun(0) == 3
    assert observer.turns_begun(3) == 3


def test_a_turn_counts_as_soon_as_it_begins():
    """A game won on your fifth turn was won on your fifth turn, whether or not
    that turn ever got to end - and a game that ends on it never does."""
    observer = Observer(new_record(0, 0, 4), hero=0)
    game = FakeGame()
    began(observer, game, 0)
    assert observer.turns_begun(0) == 1


def test_a_seat_that_never_played_is_zero_rather_than_missing():
    observer = Observer(new_record(0, 0, 4), hero=0)
    assert observer.turns_begun(2) == 0


def test_an_eliminated_seat_stops_counting():
    """Which is exactly why dividing the global counter by the number of seats
    gives the wrong answer: the divisor stops being true."""
    observer = Observer(new_record(0, 0, 4), hero=0)
    game = FakeGame()
    for _ in range(5):
        began(observer, game, 0)
        began(observer, game, 1)
    for _ in range(4):              # player 1 is out; only 0 keeps going
        began(observer, game, 0)

    assert observer.turns_begun(0) == 9
    assert observer.turns_begun(1) == 5
    global_counter = 14
    assert global_counter / 2 != observer.turns_begun(0), "the division drifts"


# ---------------------------------------------------------------------------
# What the report says
# ---------------------------------------------------------------------------


def test_the_average_is_over_the_hero_s_own_turns():
    report = Report(games=3, hero=0)
    report.win_rounds = [5, 7, 9]
    report.win_turns = [19, 27, 35]

    assert report.average_win_round == 7.0
    assert report.average_win_turn == 27.0


def test_both_numbers_are_kept_because_they_answer_different_questions():
    """The player-turn counter is what the turn cap is in, and what a log
    line refers to. It is just not what to put in front of a person."""
    report = Report(games=1, hero=0)
    report.win_rounds = [6]
    report.win_turns = [23]
    assert report.average_win_round != report.average_win_turn


def test_no_wins_is_zero_rather_than_a_division_by_none():
    assert Report(games=4, hero=0).average_win_round == 0.0


def test_the_text_report_leads_with_the_player_s_own_turn():
    from mtgfish.sim.stats import render

    report = Report(games=2, hero=0)
    report.seats = 4
    report.wins = 1
    report.win_rounds = [6]
    report.win_turns = [23]
    line = next(l for l in render(report).splitlines() if "win turn" in l.lower())
    assert "6.0" in line
    assert "23.0" in line and "player-turns" in line
    assert line.index("6.0") < line.index("23.0"), "the one they mean comes first"


# ---------------------------------------------------------------------------
# End to end, against a real game
# ---------------------------------------------------------------------------


def test_the_recorded_round_is_the_hero_s_turn_count(card_db, tmp_path):
    """Cross-checked against the replay, which counts the same turns a wholly
    different way - off the log, per seat."""
    from mtgfish.sim import RunConfig, replay_game, run

    deck = "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n"
    config = RunConfig(decklists=(deck,) * 4, games=2, max_rounds=4)
    result = run(config)

    for record in result.records:
        view = replay_game(config, record.index, boards=False)
        hero = next(seat for seat in view.seats if seat.is_hero)
        assert record.rounds == hero.turns_taken
        assert record.rounds <= record.turns
