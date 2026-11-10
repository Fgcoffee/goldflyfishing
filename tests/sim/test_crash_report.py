"""A game that crashes is left out and reported, not fatal to the run."""

from __future__ import annotations

import pytest

from mtgfish.sim import runner
from mtgfish.sim.records import GameRecord
from mtgfish.sim.runner import GameCrashed, RunConfig
from mtgfish.sim.stats import summarize


def fake_play(crash_on):
    def play(config, index, **kwargs):
        if index in crash_on:
            raise IndexError("tuple index out of range")
        record = GameRecord(index=index, seed=config.seed_for(index), turns=10)
        record.winner = 0
        return record

    return play


def test_a_crashed_game_is_left_out_and_named(monkeypatch):
    monkeypatch.setattr(runner, "play_one", fake_play({1}))
    config = RunConfig(decklists=("",) * 4, games=3, run_seed=5, workers=1)
    result = runner.run(config)

    assert [r.index for r in result.records] == [0, 2]
    [crash] = result.crashed
    assert crash.index == 1 and crash.seed == config.seed_for(1)
    assert crash.error == "IndexError: tuple index out of range"
    assert "Traceback" in crash.detail

    report = summarize(result)
    assert report.games == 2
    assert report.crashed == [{"index": 1, "seed": config.seed_for(1),
                               "error": "IndexError: tuple index out of range"}]


def test_a_worker_chunk_returns_crashes_instead_of_raising(monkeypatch):
    monkeypatch.setattr(runner, "play_one", fake_play({4}))
    config = RunConfig(decklists=("",) * 4, games=6, run_seed=1)
    items = runner._play_chunk((config, [3, 4, 5]))
    assert [type(i).__name__ for i in items] == ["GameRecord", "CrashedGame", "GameRecord"]


def test_a_run_where_every_game_crashes_still_fails(monkeypatch):
    monkeypatch.setattr(runner, "play_one", fake_play({0, 1}))
    config = RunConfig(decklists=("",) * 4, games=2, run_seed=0, workers=1)
    with pytest.raises(GameCrashed, match="every game crashed"):
        runner.run(config)


def test_cancelling_is_still_a_cancel(monkeypatch):
    def cancelled(config, index, **kwargs):
        raise runner.RunCancelled("stopped")

    monkeypatch.setattr(runner, "play_one", cancelled)
    with pytest.raises(runner.RunCancelled):
        runner.run(RunConfig(decklists=("",) * 4, games=2, workers=1))
