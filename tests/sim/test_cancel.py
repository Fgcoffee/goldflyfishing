"""Stopping a run.

A run that cannot be stopped outlives the window, the web page and the server
that started it: its worker processes play on until their games end, and one
game once took over ten minutes. These check that stopping is prompt and leaves
nothing running.
"""

from __future__ import annotations

import multiprocessing
import threading
import time

import pytest

from mtgfish.sim.runner import RunCancelled, RunConfig, play_one, run

DECK = "\n".join(
    ["1 Llanowar Elves", "1 Grizzly Bears", "1 Sol Ring", "1 Giant Growth", "60 Forest"]
    + ["", "// Commander", "1 Marwyn, the Nurturer"]
)


def _config(**overrides) -> RunConfig:
    return RunConfig(decklists=(DECK,) * 4, **overrides)


def test_watching_for_a_stop_does_not_change_the_game(card_db):
    """The cancel check rides on the observer; the game must not notice."""
    config = _config(games=1, run_seed=3)
    plain = play_one(config, 0)
    watched = play_one(config, 0, cancel=threading.Event())
    assert watched.digest == plain.digest


def test_a_game_stops_part_way_through(card_db):
    """Not at the end of the game: a slow game is exactly the one to stop."""
    stop = threading.Event()
    stop.set()
    with pytest.raises(RunCancelled):
        play_one(_config(games=1), 0, cancel=stop)


def test_a_single_process_run_stops(card_db):
    stop = threading.Event()
    stop.set()
    with pytest.raises(RunCancelled):
        run(_config(games=3, workers=1), cancel=stop)


def test_a_parallel_run_stops_and_leaves_no_workers(card_db):
    stop = threading.Event()

    def first_progress(done, total):
        stop.set()

    started = time.perf_counter()
    with pytest.raises(RunCancelled):
        run(_config(games=400, workers=2), progress=first_progress, cancel=stop)
    stopped_after = time.perf_counter() - started

    deadline = time.time() + 10
    while multiprocessing.active_children() and time.time() < deadline:
        time.sleep(0.1)
    assert multiprocessing.active_children() == []
    # Far short of 400 games: it stopped, rather than finishing and then raising.
    assert stopped_after < 180
