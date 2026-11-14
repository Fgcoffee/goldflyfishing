"""The spot check of browser runs: a sample replayed in Node, digests compared
(browser_runs.check_run / check_pending)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mtgfish.web import browser_runs, engine_build
from mtgfish.web.browser_runs import check_pending, check_run, node_binary, node_replayer, replay_cli, sample, seed_for
from mtgfish.web.db import Database
from mtgfish.web.models import BrowserRun

SAMPLES = Path(__file__).resolve().parents[2] / "schema" / "samples"


class FakeCards:
    """An IR export directory: card_path, as ir.IrStore has it."""

    def __init__(self, root: Path, documents: dict[str, dict]) -> None:
        self.root = root
        for oracle_id, document in documents.items():
            (root / f"{oracle_id}.json").write_text(json.dumps(document), encoding="utf-8")

    def card_path(self, oracle_id: str):
        path = self.root / f"{oracle_id}.json"
        return path if path.is_file() else None


@pytest.fixture
def database(tmp_path):
    return Database(f"sqlite:///{(tmp_path / 'web.sqlite').as_posix()}")


@pytest.fixture(autouse=True)
def _server_runs_engine_1(monkeypatch):
    monkeypatch.setattr(engine_build, "current", lambda: "engine-1")


def a_row(records, crashed=(), decks=None, build="engine-1", run_seed=7, max_rounds=25) -> BrowserRun:
    decks = decks or [{"commanders": ["c0"], "cards": [{"oracle_id": "a", "card_hash": "h", "count": 99}]}]
    return BrowserRun(
        run_seed=run_seed, game_start=0, game_end=len(records) + len(crashed), games=len(records),
        crashed=len(crashed), complete=True, hero=0, max_rounds=max_rounds, engine_build=build,
        ir_build="ir-1", snapshot="snap", decks=json.dumps(decks),
        payload=gzip.compress(json.dumps({"records": list(records), "crashed": list(crashed)}).encode()),
        spot_check="pending",
    )


def game(index, digest=None):
    return {"index": index, "seed": seed_for(7, index), "digest": digest or f"{index:016x}"}


def replays_to(digests: dict[int, str | None]):
    """A replayer giving these digests (None: the game throws)."""
    seen = []

    def replay(request):
        seen.append(request)
        return [
            {"index": g["index"], "seed": g["seed"], "digest": digests[g["index"]]}
            if digests[g["index"]] is not None
            else {"index": g["index"], "seed": g["seed"], "error": "TypeError: boom"}
            for g in request["games"]
        ]

    replay.seen = seen
    return replay


@pytest.fixture
def cards(tmp_path):
    root = tmp_path / "ir"
    root.mkdir()
    return FakeCards(root, {"a": {"oracle_id": "a", "name": "A", "card_hash": "h"},
                            "c0": {"oracle_id": "c0", "name": "C", "card_hash": "x"}})


def test_digests_that_match_are_ok(cards):
    run = a_row([game(0), game(1)])
    run.id = "r1"
    replay = replays_to({0: f"{0:016x}", 1: f"{1:016x}"})
    assert check_run(run, cards, replay)[0] == "ok"
    request = replay.seen[0]
    assert request["setup"]["runSeed"] == 7 and request["setup"]["maxRounds"] == 25
    assert set(request["setup"]["cards"]) == {"a", "c0"}
    assert request["setup"]["decks"][0]["cards"] == [{"oracle_id": "a", "count": 99}]


def test_a_digest_that_differs_is_a_mismatch(cards):
    run = a_row([game(0), game(1)])
    run.id = "r2"
    verdict, detail = check_run(run, cards, replays_to({0: f"{0:016x}", 1: "ffff"}))
    assert verdict == "mismatch" and "game 1" in detail


def test_a_crash_must_crash_again(cards):
    run = a_row([game(0)], crashed=[{"index": 1, "seed": seed_for(7, 1), "error": "x"}])
    run.id = "r3"
    assert check_run(run, cards, replays_to({0: f"{0:016x}", 1: None}))[0] == "ok"
    assert check_run(run, cards, replays_to({0: f"{0:016x}", 1: "abcd"}))[0] == "mismatch"


def test_a_changed_card_or_another_build_is_unverifiable(cards):
    changed = a_row([game(0)], decks=[{"commanders": [], "cards": [{"oracle_id": "a", "card_hash": "old", "count": 1}]}])
    changed.id = "r4"
    assert check_run(changed, cards, replays_to({0: "x"}))[0] == "unverifiable"
    missing = a_row([game(0)], decks=[{"commanders": ["gone"], "cards": []}])
    missing.id = "r5"
    assert check_run(missing, cards, replays_to({0: "x"}))[0] == "unverifiable"
    other = a_row([game(0)], build="engine-0")
    other.id = "r6"
    assert check_run(other, cards, replays_to({0: "x"}))[0] == "unverifiable"


def test_the_sample_is_small_and_the_same_every_time():
    games = [game(i) for i in range(50)]
    first = sample("run-a", games)
    assert len(first) == browser_runs.SAMPLE_GAMES
    assert first == sample("run-a", games)
    assert [g["index"] for g in first] == sorted(g["index"] for g in first)
    assert sample("run-a", games[:3]) == games[:3]


def test_check_pending_records_verdicts_and_leaves_failures_pending(database, cards):
    with database.session() as db:
        good, bad, broken = a_row([game(0)]), a_row([game(0, "beef")]), a_row([game(5)])
        db.add_all([good, bad, broken])
        db.commit()
        ids = good.id, bad.id, broken.id

    def replay(request):
        if request["games"][0]["index"] == 5:
            raise RuntimeError("node died")
        return [{"index": 0, "seed": seed_for(7, 0), "digest": f"{0:016x}"}]

    assert check_pending(database, cards, replay) == 3
    with database.session() as db:
        verdicts = [db.get(BrowserRun, i).spot_check for i in ids]
        assert verdicts == ["ok", "mismatch", "pending"]
        assert "replayed" in db.get(BrowserRun, ids[0]).spot_check_detail
    # Only pending runs are looked at again.
    assert check_pending(database, cards, replay) == 1


@pytest.mark.skipif(node_binary() is None or not replay_cli().is_file(),
                    reason="needs node and engine/dist/replay.cjs (npm run build:replay)")
def test_the_real_replay_cli_end_to_end(tmp_path):
    """The bench decks through the engine's Node CLI: a run's own digests are
    ok, a tampered one is a mismatch."""
    documents, decks = {}, None
    for path in SAMPLES.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "decks.json":
            decks = data
        else:
            documents[data["oracle_id"]] = data
    root = tmp_path / "ir"
    root.mkdir()
    store = FakeCards(root, documents)
    seats = [{"commanders": decks[k]["commanders"],
              "cards": [{"oracle_id": c["oracle_id"], "card_hash": documents[c["oracle_id"]]["card_hash"],
                         "count": c["count"]} for c in decks[k]["cards"]]}
             for k in ("aggro", "combo", "control", "stax")]
    setup = {"decks": [{"commanders": s["commanders"], "cards": [{"oracle_id": c["oracle_id"], "count": c["count"]}
                                                                 for c in s["cards"]]} for s in seats],
             "cards": documents, "runSeed": 7, "maxRounds": 4}
    played = node_replayer({"setup": setup, "games": [{"index": i, "seed": seed_for(7, i)} for i in range(3)]})
    records = [{"index": r["index"], "seed": r["seed"], "digest": r["digest"]} for r in played]

    run = a_row(records, decks=seats, max_rounds=4)
    run.id = "real"
    assert check_run(run, store)[0] == "ok"
    records[1] = dict(records[1], digest="0" * 16)
    tampered = a_row(records, decks=seats, max_rounds=4)
    tampered.id = "real-2"
    assert check_run(tampered, store)[0] == "mismatch"
