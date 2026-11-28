"""A game that crashes is left out and reported, not fatal to the run."""

from __future__ import annotations

import pytest

from mtgfish.sim import runner
from mtgfish.sim.records import GameRecord
from mtgfish.sim.runner import GameCrashed, RunConfig
from mtgfish.sim.stats import render, summarize
from mtgfish.tools import simulate


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


def test_the_text_report_lists_crashed_games(monkeypatch):
    monkeypatch.setattr(runner, "play_one", fake_play({1}))
    config = RunConfig(decklists=("",) * 4, games=3, run_seed=5, workers=1)
    text = render(summarize(runner.run(config)))

    assert "Games:      2" in text
    assert "1 of 3 games crashed (left out of every number above):" in text
    assert (f"game 1  seed {config.seed_for(1)}  IndexError: tuple index out of range"
            in text)


def test_a_clean_run_says_nothing_about_crashes(monkeypatch):
    monkeypatch.setattr(runner, "play_one", fake_play(set()))
    config = RunConfig(decklists=("",) * 4, games=2, run_seed=5, workers=1)
    assert "crashed" not in render(summarize(runner.run(config)))


class _FakeDb:
    content_hash = "test"

    def registry(self):
        return None


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """``simulate.main`` with no card database and a fake game."""
    monkeypatch.setattr(simulate, "CardDatabase", _FakeDb)
    monkeypatch.setattr(simulate, "_inert_cards", lambda db, decklist: [])
    deck = tmp_path / "deck.txt"
    deck.write_text("1 Island\n", encoding="utf8")

    def main(crash_on, *flags):
        monkeypatch.setattr(runner, "play_one", fake_play(crash_on))
        return simulate.main([str(deck), "--games", "3", "--workers", "1",
                              "--no-verify", *flags])

    return main


def test_simulate_exits_zero_on_crashes_without_the_flag(cli, capsys):
    assert cli({1}) == 0
    assert "1 of 3 games crashed" in capsys.readouterr().out


def test_simulate_fails_on_crash_when_asked(cli):
    assert cli({1}, "--fail-on-crash") == simulate.CRASHED_EXIT


def test_simulate_passes_a_clean_run_with_the_flag(cli):
    assert cli(set(), "--fail-on-crash") == 0


def test_simulate_fails_when_every_game_crashed(cli, capsys):
    assert cli({0, 1, 2}) == simulate.CRASHED_EXIT
    assert "every game crashed" in capsys.readouterr().err


def test_cancelling_is_still_a_cancel(monkeypatch):
    def cancelled(config, index, **kwargs):
        raise runner.RunCancelled("stopped")

    monkeypatch.setattr(runner, "play_one", cancelled)
    with pytest.raises(runner.RunCancelled):
        runner.run(RunConfig(decklists=("",) * 4, games=2, workers=1))
