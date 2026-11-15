"""Browser runs sent back to the server: /api/browser-runs."""
# The fixtures are imported from test_library, so the test parameters named
# after them read as redefinitions to ruff.
# ruff: noqa: F811

from __future__ import annotations

import gzip
import json

import pytest

from mtgfish.sim.runner import RunConfig
from mtgfish.web.browser_runs import MAX_EXPANDED, BadRun, expand, seed_for, validate

from .test_library import make_client, post, signed_up  # noqa: F401 - fixtures

JSON = {"Content-Type": "application/json"}


def seed(run_seed: int, index: int) -> int:
    return RunConfig(decklists=(), run_seed=run_seed).seed_for(index)


def a_run(games=3, run_seed=7, start=0, complete=True, crash_at=None) -> dict:
    records, crashed = [], []
    for index in range(start, start + games):
        if index == crash_at:
            crashed.append({"index": index, "seed": seed(run_seed, index),
                            "error": "TypeError: x is undefined", "detail": "at play (stub.ts:1)"})
        else:
            records.append({"index": index, "seed": seed(run_seed, index), "turns": 40, "rounds": 10,
                            "winner": 0, "winReason": "LIFE", "eliminations": [],
                            "commanderLanded": [3, None, None, None], "mulligans": [0, 0, 0, 0],
                            "openingHand": [], "drawn": ["Sol Ring"], "cast": [],
                            "digest": f"{index:016x}"})
    deck = {"commanders": ["c0"], "cards": [{"oracle_id": "a", "card_hash": "h", "count": 99}]}
    return {
        "run_seed": run_seed, "game_start": start, "game_end": start + games, "hero": 0,
        "max_rounds": 25, "complete": complete, "engine_build": "engine-1", "ir_build": "ir-1",
        "snapshot": "snap", "decks": [deck] * 4, "records": records, "crashed": crashed,
    }


def upload(client, run, gz=True):
    body = json.dumps(run).encode()
    headers = dict(JSON)
    if gz:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return client.post("/api/browser-runs", content=body, headers=headers)


# -- checks on arrival --------------------------------------------------------


def test_the_seed_matches_the_python_runner():
    assert [seed_for(12345, i) for i in range(5)] == [seed(12345, i) for i in range(5)]


def test_a_good_run_validates():
    run = validate(a_run(games=4, crash_at=2))
    assert len(run["records"]) == 3 and len(run["crashed"]) == 1


@pytest.mark.parametrize("change, message", [
    (lambda r: r["records"][0].update(seed=1), "does not have its seed"),
    (lambda r: r["records"].append(dict(r["records"][0])), "appears twice"),
    (lambda r: r["records"][0].update(index=99), "index must be"),
    (lambda r: r.update(run_seed=-1), "run_seed"),
    (lambda r: r.update(run_seed=2**32), "run_seed"),
    (lambda r: r.update(hero=4), "hero"),
    (lambda r: r.update(decks=[]), "decks"),
    (lambda r: r.update(complete="yes"), "complete"),
    (lambda r: r.update(engine_build="x" * 81), "engine_build"),
    (lambda r: r.update(records=[], crashed=[], complete=False), "no games"),
    (lambda r: r["records"].pop(), "every game"),
])
def test_a_bad_run_is_refused_whole(change, message):
    run = a_run()
    change(run)
    with pytest.raises(BadRun, match=message):
        validate(run)


def test_a_partial_run_may_leave_games_out():
    run = a_run(games=5, complete=False)
    run["records"] = run["records"][:2]
    assert len(validate(run)["records"]) == 2


def test_a_zip_bomb_stops_at_the_limit():
    bomb = gzip.compress(b"0" * (MAX_EXPANDED + 10))
    with pytest.raises(BadRun, match="too large"):
        expand(bomb, "gzip")
    with pytest.raises(BadRun, match="not valid gzip"):
        expand(b"not gzip", "gzip")


# -- the endpoint -------------------------------------------------------------


def test_an_anonymous_run_is_stored_linked_to_no_one(make_client):
    from mtgfish.web.models import BrowserRun

    client = make_client()
    reply = upload(client, a_run(games=4, crash_at=1))
    assert reply.status_code == 201
    body = reply.json()
    assert body["games"] == 3 and body["crashed"] == 1
    assert body["spot_check"] == "pending" and body["signed_in"] is False
    with client.database.session() as db:
        row = db.get(BrowserRun, body["id"])
        assert row.user_id is None and row.run_seed == 7 and row.complete is True
        assert row.engine_build == "engine-1" and row.ir_build == "ir-1" and row.snapshot == "snap"
        games = json.loads(gzip.decompress(row.payload))
        assert [r["index"] for r in games["records"]] == [0, 2, 3]
        assert games["crashed"][0]["detail"] == "at play (stub.ts:1)"
        assert json.loads(row.decks)[0]["cards"][0]["card_hash"] == "h"


def test_plain_json_is_accepted_too(make_client):
    assert upload(make_client(), a_run(), gz=False).status_code == 201


def test_a_signed_in_run_belongs_to_the_account_and_is_listed(make_client):
    client = signed_up(make_client, "runs@example.com")
    assert upload(client, a_run()).json()["signed_in"] is True
    runs = client.get("/api/browser-runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["games"] == 3 and runs[0]["spot_check"] == "pending"


def test_listing_needs_a_sign_in(make_client):
    assert make_client().get("/api/browser-runs").status_code == 401


def test_your_run_opens_whole_for_the_results_and_replay_screens(make_client):
    client = signed_up(make_client, "open@example.com")
    run_id = upload(client, a_run(games=3, crash_at=1)).json()["id"]
    body = client.get(f"/api/browser-runs/{run_id}").json()
    assert body["run"]["id"] == run_id and body["run"]["max_rounds"] == 25
    assert [r["index"] for r in body["records"]] == [0, 2]
    assert body["crashed"][0]["index"] == 1
    # Card "a" isn't in any IR export, so its games can't be replayed: said, not hidden.
    assert body["setup"] is None and "not in this server's IR export" in body["unverifiable"]


def test_a_run_opens_with_its_setup_when_the_cards_still_match(make_client, monkeypatch, tmp_path):
    from mtgfish.web import browser_runs

    monkeypatch.setattr(browser_runs, "exported_cards", lambda store, decks: {"a": {"card_hash": "h"}})
    client = signed_up(make_client, "setup@example.com")
    run_id = upload(client, a_run()).json()["id"]
    setup = client.get(f"/api/browser-runs/{run_id}").json()["setup"]
    assert setup["runSeed"] == 7 and setup["maxRounds"] == 25 and setup["cards"] == {"a": {"card_hash": "h"}}
    assert setup["decks"][0] == {"commanders": ["c0"], "cards": [{"oracle_id": "a", "count": 99}]}


def test_only_your_own_runs_open(make_client):
    owner = signed_up(make_client, "owner@example.com")
    run_id = upload(owner, a_run()).json()["id"]
    assert signed_up(make_client, "other@example.com").get(f"/api/browser-runs/{run_id}").status_code == 404
    assert make_client().get(f"/api/browser-runs/{run_id}").status_code == 401
    anonymous = upload(make_client(), a_run()).json()["id"]
    assert owner.get(f"/api/browser-runs/{anonymous}").status_code == 404


def test_the_export_includes_browser_runs(make_client):
    client = signed_up(make_client, "export@example.com")
    upload(client, a_run(games=2))
    exported = client.get("/auth/account/export").json()
    [run] = exported["browser_runs"]
    assert run["run_seed"] == 7 and len(run["records"]) == 2 and run["decks"][0]["commanders"] == ["c0"]


def test_deleting_the_account_deletes_its_browser_runs(make_client):
    from mtgfish.web.models import BrowserRun

    client = signed_up(make_client, "gone2@example.com")
    upload(client, a_run())
    upload(make_client(), a_run())  # someone else's, anonymous
    post(client, "/auth/account/delete", {"confirm": "DELETE"})
    with client.database.session() as db:
        assert db.query(BrowserRun).count() == 1


def test_anonymous_runs_expire(make_client):
    import datetime as dt

    from mtgfish.web.auth.service import purge_expired
    from mtgfish.web.browser_runs import ANONYMOUS_DAYS
    from mtgfish.web.models import BrowserRun

    client = make_client()
    upload(client, a_run())
    with client.database.session() as db:
        row = db.query(BrowserRun).one()
        row.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=ANONYMOUS_DAYS + 1)
        db.commit()
        assert purge_expired(db)["anonymous_runs"] == 1
        assert db.query(BrowserRun).count() == 0


def test_bad_uploads_are_refused(make_client):
    client = make_client()
    tampered = a_run()
    tampered["records"][1]["seed"] += 1
    assert upload(client, tampered).status_code == 400
    assert client.post("/api/browser-runs", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/browser-runs", content=b"nope", headers=JSON).status_code == 400
    bomb = gzip.compress(b" " * (MAX_EXPANDED + 10))
    reply = client.post("/api/browser-runs", content=bomb, headers=JSON | {"Content-Encoding": "gzip"})
    assert reply.status_code == 413


def test_uploads_are_rate_limited(make_client):
    client = make_client()
    for _ in range(30):
        client.post("/api/browser-runs", content=b"{}", headers=JSON)
    assert client.post("/api/browser-runs", content=b"{}", headers=JSON).status_code == 429


# -- engine builds ------------------------------------------------------------


@pytest.fixture(autouse=True)
def _server_runs_engine_1(monkeypatch):
    """The server's engine build, as the runs above were played with."""
    from mtgfish.web import engine_build

    monkeypatch.setattr(engine_build, "current", lambda: "engine-1")


def test_a_run_from_another_engine_build_is_refused_with_reload(make_client, monkeypatch):
    from mtgfish.web import engine_build

    monkeypatch.setattr(engine_build, "current", lambda: "engine-2")
    reply = upload(make_client(), a_run())
    assert reply.status_code == 409
    assert reply.json()["reload"] is True and reply.json()["engine_build"] == "engine-2"


def test_a_server_without_a_known_build_does_not_gate(make_client, monkeypatch):
    from mtgfish.web import engine_build

    monkeypatch.setattr(engine_build, "current", lambda: None)
    assert upload(make_client(), a_run()).status_code == 201


def test_python_and_the_webapp_compute_the_same_engine_build():
    import shutil
    import subprocess

    from mtgfish.web.engine_build import ENGINE_SRC, fingerprint

    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = ENGINE_SRC.parent.parent / "webapp" / "engine-build.mjs"
    if not ENGINE_SRC.is_dir() or not shutil.which(node):
        pytest.skip("needs the engine source and Node")
    printed = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)
    assert printed.stdout.strip() == fingerprint(ENGINE_SRC)


def test_the_fingerprint_ignores_tests_and_line_endings(tmp_path):
    from mtgfish.web.engine_build import fingerprint

    (tmp_path / "sim").mkdir()
    (tmp_path / "sim" / "a.ts").write_bytes(b"export const a = 1;\n")
    first = fingerprint(tmp_path)
    (tmp_path / "sim" / "a.test.ts").write_text("test", encoding="utf-8")
    (tmp_path / "sim" / "a.ts").write_bytes(b"export const a = 1;\r\n")
    assert fingerprint(tmp_path) == first
    (tmp_path / "sim" / "a.ts").write_bytes(b"export const a = 2;\n")
    assert fingerprint(tmp_path) != first


def test_decklists_resolve_for_a_browser_run(make_client):
    """The browser path sends decklist text to /api/ir/decks, so the server
    turns references (Archidekt links) into text first; a pasted list passes
    through unchanged."""
    client = make_client()
    reply = client.post("/api/call/resolve_decklists", json={"args": [json.dumps(["1 Sol Ring", "1 Island"])]})
    assert reply.status_code == 200
    # A pasted list has no name of its own; History names it by its commander.
    assert reply.json() == {"decklists": ["1 Sol Ring", "1 Island"], "names": ["", ""]}


# -- History: what a browser run is called, and its win rate ------------------


def test_history_names_a_run_by_its_archidekt_deck_and_scores_it(make_client):
    client = signed_up(make_client, "named@example.com")
    run = a_run(games=4, crash_at=1)
    run["records"][0]["winner"] = 2
    run["deck_name"] = "Mono-Green Stompy"
    upload(client, run)
    listed = client.get("/api/browser-runs").json()["runs"][0]
    assert listed["label"] == "Mono-Green Stompy"
    # Wins over recorded games, the crashed one left out, as the report counts.
    assert listed["win_rate"] == pytest.approx(2 / 3)


def test_history_falls_back_to_the_commander_of_the_pasted_list(make_client):
    client = signed_up(make_client, "pasted@example.com")
    run = a_run()
    run["hero_decklist"] = "1 Sol Ring\n\n// Commander\n1 Atraxa, Praetors' Voice"
    upload(client, run)
    assert client.get("/api/browser-runs").json()["runs"][0]["label"] == "Atraxa, Praetors' Voice"


def test_history_names_a_saved_deck_by_its_version(make_client):
    from .test_library import DECK

    client = signed_up(make_client, "saved@example.com")
    post(client, "/api/decks", {"name": "My Atraxa", "decklist": DECK})
    run = a_run()
    run["hero_decklist"] = DECK
    run["deck_name"] = "ignored when the list is a saved deck"
    upload(client, run)
    assert client.get("/api/browser-runs").json()["runs"][0]["label"] == "My Atraxa v1"


def test_runs_stored_before_labels_get_them_when_listed(make_client):
    from mtgfish.web.models import BrowserRun

    client = signed_up(make_client, "older@example.com")
    run_id = upload(client, a_run()).json()["id"]
    with client.accounts.db.session() as db:
        row = db.get(BrowserRun, run_id)
        row.label, row.win_rate = None, None
        db.commit()
    listed = client.get("/api/browser-runs").json()["runs"][0]
    # Its commander "c0" isn't in any IR export, so nothing better to call it.
    assert listed["label"] == "Unsaved deck" and listed["win_rate"] == 1.0
    with client.accounts.db.session() as db:
        assert db.get(BrowserRun, run_id).win_rate == 1.0  # kept, not recomputed each time


def test_a_deck_name_too_long_is_refused():
    run = a_run()
    run["deck_name"] = "x" * 201
    with pytest.raises(BadRun, match="deck_name"):
        validate(run)
