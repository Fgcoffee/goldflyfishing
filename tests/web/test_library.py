"""Saved decks and run history: versions, recording, and - above all - that
one person can never reach another's."""

from __future__ import annotations

import json

import pytest

from mtgfish.paths import card_db_path

DECK = "1 Sol Ring\n1 Arcane Signet\n\n// Commander\n1 Atraxa, Praetors' Voice"
DECK_V2 = "1 Sol Ring\n1 Mind Stone\n\n// Commander\n1 Atraxa, Praetors' Voice"


@pytest.fixture
def make_client(tmp_path):
    if not card_db_path().exists():
        pytest.skip("card database not built")
    from fastapi.testclient import TestClient

    from mtgfish.web.app import create_app
    from mtgfish.web.auth import Accounts, AuthSettings, LogMailer
    from mtgfish.web.db import Database
    from mtgfish.web.settings import Settings

    database = Database(f"sqlite:///{(tmp_path / 'web.sqlite').as_posix()}")
    clients = []

    def build():
        accounts = Accounts(
            database,
            AuthSettings(production=False, session_secret="x" * 40, public_base_url="http://testserver"),
            mailer=LogMailer(),
        )
        app = create_app(Settings(), accounts=accounts)
        client = TestClient(app, base_url="http://testserver")
        client.__enter__()
        client.database = database
        client.accounts = accounts
        client.app_state = app.state.mtgfish
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def headers(client):
    client.get("/auth/me")
    return {"X-CSRF-Token": client.cookies.get("mtgfish_csrf"), "Content-Type": "application/json"}


def post(client, path, payload=None):
    return client.post(path, json=payload or {}, headers=headers(client))


def signed_up(make_client, email, verified=True):
    import re

    client = make_client()
    post(client, "/auth/register", {"email": email, "password": "correct horse battery", "display_name": email[:4]})
    if verified:
        text = client.accounts.mailer().sent[-1][2]
        token = re.search(r"verify\?token=([\w\-%]+)", text).group(1)
        post(client, "/auth/verify", {"token": token})
    return client


def fake_run(client, decklist=DECK, seed=7, win_rate=0.25):
    """Record a run as the server does when a signed-in user's run finishes."""
    from mtgfish.sim.runner import RunConfig
    from mtgfish.web.library import record_run

    user_id = client.get("/auth/me").json()["user"]["id"]
    config = RunConfig(decklists=(decklist, decklist, decklist, decklist), games=10, run_seed=seed,
                       card_db_hash="abc")
    return record_run(client.database, user_id, config, {"win_rate": win_rate, "games": 10})


def test_decks_save_load_rename_and_version(make_client):
    client = signed_up(make_client, "a@example.com")
    created = post(client, "/api/decks", {"name": "Atraxa", "decklist": DECK})
    assert created.status_code == 201
    deck = created.json()["deck"]
    assert deck["commander"] == "Atraxa, Praetors' Voice" and deck["version"] == 1

    same = post(client, f"/api/decks/{deck['id']}", {"decklist": DECK + "\n"})
    assert same.json()["deck"]["version"] == 1  # unchanged list: no new version
    changed = post(client, f"/api/decks/{deck['id']}", {"decklist": DECK_V2, "name": "Atraxa 2"})
    assert changed.json()["deck"]["version"] == 2

    got = client.get(f"/api/decks/{deck['id']}").json()
    assert got["decklist"] == DECK_V2 and got["deck"]["name"] == "Atraxa 2"
    assert [v["number"] for v in got["versions"]] == [2, 1]
    assert client.get(f"/api/decks/{deck['id']}/versions/1").json()["decklist"] == DECK

    assert post(client, f"/api/decks/{deck['id']}/delete").json()["deleted"]
    assert client.get("/api/decks").json()["decks"] == []


def test_unverified_accounts_cannot_save(make_client):
    client = signed_up(make_client, "u@example.com", verified=False)
    reply = post(client, "/api/decks", {"name": "x", "decklist": DECK})
    assert reply.status_code == 403
    assert fake_run(client) is None  # no history either
    assert client.get("/api/runs").json()["runs"] == []


def test_signed_out_gets_401(make_client):
    client = make_client()
    assert client.get("/api/decks").status_code == 401
    assert client.get("/api/runs").status_code == 401


def test_runs_record_the_deck_version_they_played(make_client):
    client = signed_up(make_client, "r@example.com")
    deck = post(client, "/api/decks", {"name": "Atraxa", "decklist": DECK}).json()["deck"]
    run_id = fake_run(client, DECK, seed=11)
    post(client, f"/api/decks/{deck['id']}", {"decklist": DECK_V2})
    later = fake_run(client, DECK_V2, seed=12)

    runs = {r["id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert runs[run_id]["deck"] == "Atraxa v1" and runs[later]["deck"] == "Atraxa v2"
    full = client.get(f"/api/runs/{run_id}").json()["run"]
    # Everything a replay needs.
    assert full["config"]["decklists"][0] == DECK
    assert full["config"]["run_seed"] == 11 and full["config"]["card_db_hash"] == "abc"
    assert {"games", "max_rounds", "hero"} <= set(full["config"])
    assert full["report"]["win_rate"] == 0.25


def test_one_user_cannot_touch_anothers_decks_or_runs(make_client):
    alice = signed_up(make_client, "alice@example.com")
    bob = signed_up(make_client, "bob@example.com")
    deck = post(alice, "/api/decks", {"name": "Mine", "decklist": DECK}).json()["deck"]
    run_id = fake_run(alice)

    # Bob sees nothing of Alice's in his lists...
    assert bob.get("/api/decks").json()["decks"] == []
    assert bob.get("/api/runs").json()["runs"] == []
    # ...and every direct attempt looks exactly like a missing id.
    attempts = [
        bob.get(f"/api/decks/{deck['id']}"),
        bob.get(f"/api/decks/{deck['id']}/versions/1"),
        post(bob, f"/api/decks/{deck['id']}", {"name": "stolen", "decklist": DECK_V2}),
        post(bob, f"/api/decks/{deck['id']}/delete"),
        bob.get(f"/api/runs/{run_id}"),
        post(bob, f"/api/runs/{run_id}/delete"),
        post(bob, f"/api/runs/{run_id}/open"),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)

    # Alice's things are untouched.
    mine = alice.get(f"/api/decks/{deck['id']}").json()
    assert mine["deck"]["name"] == "Mine" and mine["decklist"] == DECK
    assert alice.get(f"/api/runs/{run_id}").status_code == 200


def test_deleting_an_account_deletes_its_decks_and_runs(make_client):
    from mtgfish.web.models import Deck, DeckVersion, Run

    client = signed_up(make_client, "gone@example.com")
    post(client, "/api/decks", {"name": "x", "decklist": DECK})
    fake_run(client)
    post(client, "/auth/account/delete", {"confirm": "DELETE"})
    with client.database.session() as db:
        assert db.query(Deck).count() == 0
        assert db.query(DeckVersion).count() == 0
        assert db.query(Run).count() == 0


def test_opening_a_run_lets_its_games_be_replayed(make_client):
    client = signed_up(make_client, "o@example.com")
    run_id = fake_run(client, seed=99)
    reply = post(client, f"/api/runs/{run_id}/open")
    assert reply.status_code == 200 and reply.json()["report"]["win_rate"] == 0.25
    session = next(iter(client.app_state.sessions.all()))
    config = session.bridge._config
    assert config.run_seed == 99 and config.decklists[0] == DECK


def test_a_finished_run_is_saved_to_history(make_client):
    """The hook: a signed-in session's run_finished lands in History."""
    from mtgfish.sim.runner import RunConfig

    client = signed_up(make_client, "h@example.com")
    client.post("/api/call/sandbox_state", json={"args": []})  # opens the browser session
    session = next(iter(client.app_state.sessions.all()))
    session.bridge._config = RunConfig(decklists=(DECK,) * 4, games=5, run_seed=3, card_db_hash="h")
    session.publish("run_finished", [json.dumps({"win_rate": 0.4, "games": 5})])
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["win_rate"] == 0.4 and runs[0]["run_seed"] == 3
