"""A game that crashes says which game it was, in-process as in a worker pool."""

from __future__ import annotations

import pytest

from mtgfish.sim import runner
from mtgfish.sim.runner import GameCrashed, RunConfig


def test_an_in_process_crash_names_its_game_and_seed(monkeypatch):
    real = runner.play_one

    def flaky(config, index, **kwargs):
        if index == 1:
            raise IndexError("tuple index out of range")
        return real.__wrapped__(config, index, **kwargs) if hasattr(real, "__wrapped__") else None

    monkeypatch.setattr(runner, "play_one", flaky)
    config = RunConfig(decklists=("",) * 4, games=3, run_seed=5, workers=1)
    with pytest.raises(GameCrashed) as crash:
        runner.run(config)
    message = str(crash.value)
    assert f"game 1 (seed {config.seed_for(1)})" in message
    assert "IndexError: tuple index out of range" in message
    assert "Traceback" in message or "flaky" in message
