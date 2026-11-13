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
