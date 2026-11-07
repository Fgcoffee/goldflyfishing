"""The web run's report lists the deck's unread cards, as the command line
does (the auditor's WARN: it always said "Inert cards: none")."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest


def test_a_run_through_the_bridge_reports_the_inert_cards(card_db, monkeypatch):
    bridge_module = pytest.importorskip("mtgfish.ui.bridge")
    from mtgfish.sim import RunConfig

    seen = {}

    def fake_run(config, progress=None, cancel=None):
        return SimpleNamespace(unparsed_cards=[])

    def fake_summarize(result):
        seen["unparsed"] = list(result.unparsed_cards)
        raise RuntimeError("stop here")  # the payload itself is not under test

    monkeypatch.setattr(bridge_module, "run", fake_run)
    monkeypatch.setattr(bridge_module, "summarize", fake_summarize)
    monkeypatch.setattr(
        "mtgfish.sim.stats.inert_cards", lambda db, decklist: ["Some Unread Card"]
    )

    bridge = bridge_module.Bridge()
    emitted = []
    bridge.run_finished.connect(emitted.append)
    config = RunConfig(decklists=("1 Forest",) * 4, games=1, run_seed=0)
    bridge._run_worker(config, threading.Event())
    assert seen["unparsed"] == ["Some Unread Card"]
    assert "error" in json.loads(emitted[-1])
