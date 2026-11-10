"""What the private beta launch adds: legal pages, consent, data export,
retention, robots, and the limits a small hosted instance needs."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from mtgfish.paths import card_db_path


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    if not card_db_path().exists():
        pytest.skip("card database not built")
    from fastapi.testclient import TestClient

    from mtgfish.web.app import create_app
    from mtgfish.web.auth import Accounts, AuthSettings, LogMailer
    from mtgfish.web.db import Database
    from mtgfish.web.settings import Settings

    database = Database(f"sqlite:///{(tmp_path / 'web.sqlite').as_posix()}")
    clients = []

    def build(*, production=False, mail=True):
        settings = AuthSettings(
            production=production,
            session_secret="x" * 40,
            public_base_url="https://beta.example" if production else "http://testserver",
        )
        accounts = Accounts(database, settings, mailer=LogMailer() if mail else None)
        client = TestClient(create_app(Settings(), accounts=accounts), base_url="http://testserver")
        client.__enter__()
        client.database = database
        client.accounts = accounts
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def headers(client):
    client.get("/auth/me")
    return {"X-CSRF-Token": client.cookies.get("mtgfish_csrf"), "Content-Type": "application/json"}


def register(client, **extra):
    payload = {"email": "t@example.com", "password": "correct horse battery", "display_name": "T"}
    payload.update(extra)
    return client.post("/auth/register", json=payload, headers=headers(client))


# -- search engines -----------------------------------------------------------


def test_robots_txt_keeps_crawlers_out(make_client):
    client = make_client()
    reply = client.get("/robots.txt")
    assert reply.status_code == 200
    assert "Disallow: /" in reply.text
    assert "noindex" in client.get("/").headers["x-robots-tag"]
    assert "noindex" in client.get("/healthz").headers["x-robots-tag"]


# -- legal pages ----------------------------------------------------------------


def test_legal_pages_are_filled_in_from_the_environment(make_client, monkeypatch):
    monkeypatch.setenv("OPERATOR_NAME", "Test Operator <script>")
    monkeypatch.setenv("CONTACT_EMAIL", "privacy@example.com")
    client = make_client()
    for path in ("/privacy", "/terms"):
        page = client.get(path)
        assert page.status_code == 200
        assert "privacy@example.com" in page.text
        assert "Test Operator &lt;script&gt;" in page.text  # escaped
        assert "{{" not in page.text
    assert "ICO" in client.get("/privacy").text or "ico.org.uk" in client.get("/privacy").text
    assert "England and Wales" in client.get("/terms").text
    # The raw templates are not served.
    assert client.get("/legal/privacy.html").status_code == 404
    assert client.get("/legal/legal.css").status_code == 200


# -- consent --------------------------------------------------------------------


def test_registration_requires_accepting_the_terms(make_client):
    from mtgfish.web.auth.service import TERMS_VERSION
    from mtgfish.web.models import User

    client = make_client()
    refused = register(client)
    assert refused.status_code == 400 and "16" in refused.json()["error"]
    assert register(client, accept_terms=True).status_code == 201
    with client.database.session() as db:
        user = db.query(User).one()
        assert user.terms_version == TERMS_VERSION and user.terms_accepted_at is not None


# -- data export ------------------------------------------------------------------


def test_download_my_data_holds_everything(make_client):
    import re

    client = make_client()
    register(client, accept_terms=True)
    token = re.search(r"verify\?token=([\w\-%]+)", client.accounts.mailer().sent[-1][2]).group(1)
    client.post("/auth/verify", json={"token": token}, headers=headers(client))
    client.post("/api/decks", json={"name": "Mine", "decklist": "1 Sol Ring"}, headers=headers(client))

    reply = client.get("/auth/account/export")
    assert reply.status_code == 200
    assert "attachment" in reply.headers["content-disposition"]
    data = json.loads(reply.text)
    assert data["account"]["email"] == "t@example.com"
    assert data["account"]["terms_version"]
    assert data["decks"][0]["versions"][0]["decklist"] == "1 Sol Ring"
    assert "password_hash" not in json.dumps(data)

    stranger = make_client()
    assert stranger.get("/auth/account/export").status_code == 401


# -- retention ----------------------------------------------------------------------


def test_purge_removes_what_the_policy_says_is_not_kept(make_client):
    from mtgfish.web.auth.service import purge_expired
    from mtgfish.web.models import AuthSession, User, now

    client = make_client()
    register(client, accept_terms=True)
    with client.database.session() as db:
        user = db.query(User).one()
        db.add(AuthSession(id_hash="old", user_id=user.id, expires_at=now() - dt.timedelta(days=1)))
        db.commit()
        removed = purge_expired(db)
        assert removed["sessions"] == 1 and removed["unverified_users"] == 0

        user.created_at = now() - dt.timedelta(days=31)
        db.commit()
        removed = purge_expired(db)
        assert removed["unverified_users"] == 1
        assert db.query(User).count() == 0


# -- Discord-only production -------------------------------------------------------


def test_production_without_mail_runs_discord_only(make_client, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    client = make_client(production=True, mail=False)
    me = client.get("/auth/me").json()
    assert me["email"]["enabled"] is False
    reply = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "correct horse battery",
              "display_name": "A", "accept_terms": True},
        headers=headers(client) | {"Origin": "https://beta.example"},
    )
    assert reply.status_code == 503 and "Discord" in reply.json()["error"]
    assert "strict-transport-security" in client.get("/healthz").headers


# -- small hosts -------------------------------------------------------------------


def test_game_counts_are_capped():
    from mtgfish.web.sessions import clamp_games

    assert clamp_games("start_run", ["deck", 5000, 0], 200) == ["deck", 200, 0]
    assert clamp_games("start_cutlab", ["d", "", "", False, 900, 9000, 1], 300)[4:6] == [300, 300]
    assert clamp_games("start_run", ["deck", 50, 0], 200) == ["deck", 50, 0]
    assert clamp_games("start_run", ["deck", 5000, 0], 0) == ["deck", 5000, 0]


def test_worker_count_can_be_capped(monkeypatch):
    from mtgfish.sim.runner import _default_workers

    monkeypatch.setenv("MTGFISH_WORKERS", "1")
    assert _default_workers() == 1
    monkeypatch.setenv("MTGFISH_WORKERS", "nonsense")
    assert _default_workers() >= 1


def test_render_style_database_urls_get_the_psycopg_driver(monkeypatch):
    from mtgfish.web.db import database_url

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert database_url() == "postgresql+psycopg://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    assert database_url() == "postgresql+psycopg://u:p@host/db"
