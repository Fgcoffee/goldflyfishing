"""Accounts: email + password, Discord (mocked), sessions, CSRF, limits.

Everything runs against the real FastAPI app with a fresh SQLite database per
test. Discord's token and profile endpoints are an httpx MockTransport, so no
request leaves the machine and no real secret is involved.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from mtgfish.paths import card_db_path


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    if not card_db_path().exists():
        pytest.skip("card database not built")
    from fastapi.testclient import TestClient

    from mtgfish.web.app import create_app
    from mtgfish.web.auth import Accounts, AuthSettings, LogMailer
    from mtgfish.web.auth.ratelimit import RateLimiter
    from mtgfish.web.db import Database
    from mtgfish.web.settings import Settings

    clients = []

    def build(*, discord: dict | None = None, limits=None, discord_profile=None):
        database = Database(f"sqlite:///{(tmp_path / 'web.sqlite').as_posix()}")
        settings = AuthSettings(
            production=False,
            session_secret="x" * 40,
            public_base_url="http://testserver",
            discord_client_id=(discord or {}).get("id", ""),
            discord_client_secret=(discord or {}).get("secret", ""),
        )
        seen: dict = {}

        def discord_api(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/oauth2/token":
                form = parse_qs(request.content.decode())
                seen["token_form"] = form
                if form.get("code") != ["good-code"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "discord-token"})
            if request.url.path == "/api/users/@me":
                seen["auth_header"] = request.headers.get("authorization")
                return httpx.Response(200, json=discord_profile or {
                    "id": "4242", "username": "goldie", "global_name": "Goldie",
                    "avatar": "abc", "email": "goldie@example.com", "verified": True,
                })
            return httpx.Response(404)

        mailer = LogMailer()
        accounts = Accounts(
            database, settings, mailer=mailer,
            limits=limits or RateLimiter(), http_transport=httpx.MockTransport(discord_api),
        )
        app = create_app(Settings(), accounts=accounts)
        client = TestClient(app, base_url="http://testserver")
        client.__enter__()
        clients.append(client)
        client.mailer = mailer
        client.seen = seen
        client.database = database
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def csrf(client) -> dict:
    client.get("/auth/me")
    return {"X-CSRF-Token": client.cookies.get("mtgfish_csrf"), "Content-Type": "application/json"}


def post(client, path, payload=None, headers=None):
    return client.post(path, json=payload or {}, headers=headers if headers is not None else csrf(client))


def register(client, email="ana@example.com", password="correct horse battery", name="Ana"):
    return post(client, "/auth/register", {
        "email": email, "password": password, "display_name": name, "accept_terms": True,
    })


def last_link(client, path):
    _, _, text = client.mailer.sent[-1]
    return re.search(rf"{re.escape(path)}\?token=([\w\-%]+)", text).group(1)


# -- email and password -------------------------------------------------------


def test_register_signs_in_and_sends_a_verification_link(make_client):
    client = make_client()
    reply = register(client)
    assert reply.status_code == 201
    user = reply.json()["user"]
    assert user["email"] == "ana@example.com" and not user["email_verified"]
    assert client.get("/auth/me").json()["user"]["id"] == user["id"]
    to, subject, text = client.mailer.sent[-1]
    assert to == "ana@example.com" and "/auth/verify?token=" in text


def test_passwords_are_hashed_with_argon2id(make_client):
    from mtgfish.web.models import User

    client = make_client()
    register(client)
    with client.database.session() as db:
        stored = db.query(User).one().password_hash
    assert stored.startswith("$argon2id$") and "correct horse" not in stored


@pytest.mark.parametrize("password", ["short", "1234567890", "aaaaaaaaaaaa"])
def test_weak_passwords_are_refused(make_client, password):
    reply = register(make_client(), password=password)
    assert reply.status_code == 400


def test_verify_link_is_single_use(make_client):
    client = make_client()
    register(client)
    token = last_link(client, "/auth/verify")
    # The emailed link only hands the token to the page...
    landing = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert landing.status_code == 303 and "#verify=" in landing.headers["location"]
    # ...which redeems it.
    assert post(client, "/auth/verify", {"token": token}).json()["user"]["email_verified"]
    again = post(client, "/auth/verify", {"token": token})
    assert again.status_code == 400


def test_login_logout_and_wrong_password(make_client):
    client = make_client()
    register(client)
    post(client, "/auth/logout")
    assert client.get("/auth/me").status_code == 401

    wrong = post(client, "/auth/login", {"email": "ana@example.com", "password": "not the password"})
    unknown = post(client, "/auth/login", {"email": "nobody@example.com", "password": "whatever123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # nothing says which was wrong

    ok = post(client, "/auth/login", {"email": "ANA@example.com", "password": "correct horse battery"})
    assert ok.status_code == 200
    assert client.get("/auth/me").json()["user"]["email"] == "ana@example.com"


def test_logout_ends_the_session_on_the_server(make_client):
    from mtgfish.web.models import AuthSession

    client = make_client()
    register(client)
    stolen = client.cookies.get("mtgfish_auth")
    post(client, "/auth/logout")
    with client.database.session() as db:
        assert db.query(AuthSession).count() == 0
    client.cookies.set("mtgfish_auth", stolen)
    assert client.get("/auth/me").status_code == 401


def test_reset_never_reveals_whether_an_email_exists(make_client):
    client = make_client()
    register(client)
    known = post(client, "/auth/password/forgot", {"email": "ana@example.com"})
    unknown = post(client, "/auth/password/forgot", {"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_reset_changes_the_password_and_signs_out_everywhere(make_client):
    from mtgfish.web.models import AuthSession

    client = make_client()
    register(client)
    other = make_client()
    post(other, "/auth/login", {"email": "ana@example.com", "password": "correct horse battery"})

    post(client, "/auth/password/forgot", {"email": "ana@example.com"})
    token = last_link(client, "/auth/reset")
    reply = post(client, "/auth/password/reset", {"token": token, "password": "a whole new phrase"})
    assert reply.status_code == 200

    with client.database.session() as db:
        assert db.query(AuthSession).count() == 1  # only the one the reset just made
    assert other.get("/auth/me").status_code == 401
    assert post(client, "/auth/password/reset", {"token": token, "password": "another phrase!"}).status_code == 400
    old = post(client, "/auth/login", {"email": "ana@example.com", "password": "correct horse battery"})
    assert old.status_code == 401


def test_posts_without_the_csrf_token_are_refused(make_client):
    client = make_client()
    client.get("/auth/me")
    reply = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "correct horse battery", "display_name": "A"},
    )
    assert reply.status_code == 403
    forged = client.post(
        "/auth/login", json={"email": "a@example.com", "password": "x"},
        headers={"X-CSRF-Token": "guess", "Content-Type": "application/json"},
    )
    assert forged.status_code == 403


def test_cross_site_origin_is_refused(make_client):
    client = make_client()
    headers = csrf(client) | {"Origin": "https://evil.example"}
    reply = client.post("/auth/logout", json={}, headers=headers)
    assert reply.status_code == 403
    reply = client.post("/api/call/sandbox_reset", json={"args": []}, headers=headers)
    assert reply.status_code == 403


def test_login_is_rate_limited(make_client):
    from mtgfish.web.auth.ratelimit import RateLimiter

    client = make_client(limits=RateLimiter({"login_email": (3, 300)}))
    register(client)
    codes = [
        post(client, "/auth/login", {"email": "ana@example.com", "password": "nope nope nope"}).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_account_deletion_removes_everything(make_client):
    from mtgfish.web.models import AuthSession, AuthToken, User

    client = make_client()
    register(client)
    assert post(client, "/auth/account/delete", {"confirm": "nope"}).status_code == 400
    assert post(client, "/auth/account/delete", {"confirm": "DELETE"}).json()["deleted"]
    with client.database.session() as db:
        assert db.query(User).count() == 0
        assert db.query(AuthSession).count() == 0
        assert db.query(AuthToken).count() == 0
    assert client.get("/auth/me").status_code == 401


# -- Discord ------------------------------------------------------------------

DISCORD = {"id": "1569452865821343936", "secret": "test-secret-not-real"}


def discord_round_trip(client, *, code="good-code", tamper_state=False, link=False):
    start = client.get("/auth/discord/login" + ("?link=1" if link else ""), follow_redirects=False)
    assert start.status_code == 303
    query = parse_qs(urlsplit(start.headers["location"]).query)
    state = query["state"][0]
    if tamper_state:
        state = "not-the-state"
    return start, client.get(
        f"/auth/discord/callback?code={code}&state={state}", follow_redirects=False
    )


def test_discord_login_creates_a_user_with_pkce(make_client):
    client = make_client(discord=DISCORD)
    start, done = discord_round_trip(client)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert start.headers["location"].startswith("https://discord.com/oauth2/authorize")
    assert query["client_id"] == [DISCORD["id"]]
    assert query["redirect_uri"] == ["http://testserver/auth/discord/callback"]
    assert query["scope"] == ["identify email"] and query["code_challenge_method"] == ["S256"]
    assert done.status_code == 303 and done.headers["location"] == "/"
    # The verifier went to the token endpoint, and the token to /users/@me.
    assert client.seen["token_form"]["code_verifier"]
    assert client.seen["auth_header"] == "Bearer discord-token"
    user = client.get("/auth/me").json()["user"]
    assert user["discord"]["id"] == "4242" and user["display_name"] == "Goldie"
    assert user["email"] == "goldie@example.com" and user["email_verified"]


def test_discord_rejects_a_missing_or_mismatched_state(make_client):
    client = make_client(discord=DISCORD)
    _, done = discord_round_trip(client, tamper_state=True)
    assert "auth_error" in done.headers["location"]
    assert client.get("/auth/me").status_code == 401
    assert "token_form" not in client.seen  # the code was never even exchanged

    fresh = make_client(discord=DISCORD)
    no_cookie = fresh.get("/auth/discord/callback?code=good-code&state=x", follow_redirects=False)
    assert "auth_error" in no_cookie.headers["location"]


def test_discord_keeps_no_tokens(make_client):
    from sqlalchemy import inspect

    client = make_client(discord=DISCORD)
    discord_round_trip(client)
    columns = {c["name"] for c in inspect(client.database.engine).get_columns("users")}
    assert not any("token" in name for name in columns)


def test_discord_auto_links_only_to_a_verified_matching_email(make_client):
    from mtgfish.web.models import User

    client = make_client(discord=DISCORD)
    register(client, email="goldie@example.com")
    post(client, "/auth/logout")
    discord_round_trip(client)
    with client.database.session() as db:
        assert db.query(User).count() == 2  # not verified yet: no auto-link

    linked = make_client(discord=DISCORD)
    with linked.database.session() as db:
        db.query(User).delete()
        db.commit()
    register(linked, email="goldie@example.com")
    post(linked, "/auth/verify", {"token": last_link(linked, "/auth/verify")})
    post(linked, "/auth/logout")
    discord_round_trip(linked)
    with linked.database.session() as db:
        users = db.query(User).all()
    assert len(users) == 1 and users[0].discord_id == "4242"


def test_link_and_unlink_discord(make_client):
    client = make_client(discord=DISCORD)
    register(client)
    _, done = discord_round_trip(client, link=True)
    me = client.get("/auth/me").json()["user"]
    assert me["discord"]["id"] == "4242" and set(me["login_methods"]) == {"password", "discord"}

    reply = post(client, "/auth/discord/unlink")
    assert reply.status_code == 200 and reply.json()["user"]["discord"] is None


def test_unlink_refused_when_it_would_lock_the_account_out(make_client):
    client = make_client(discord=DISCORD)
    discord_round_trip(client)  # a Discord-only account
    reply = post(client, "/auth/discord/unlink")
    assert reply.status_code == 409
    assert client.get("/auth/me").json()["user"]["discord"]["id"] == "4242"


def test_dev_fake_login_without_a_secret(make_client):
    client = make_client()
    start = client.get("/auth/discord/login", follow_redirects=False)
    assert start.headers["location"].startswith("/auth/discord/callback?code=dev")
    client.get(start.headers["location"], follow_redirects=False)
    assert client.get("/auth/me").json()["user"]["display_name"] == "Dev Discord User"


def test_production_refuses_the_fake_login_and_weak_config():
    from mtgfish.web.auth import AuthSettings, ConfigError

    with pytest.raises(ConfigError):
        AuthSettings(production=True, session_secret="short", public_base_url="https://x.example")
    settings = AuthSettings(
        production=True, session_secret="s" * 40, public_base_url="https://x.example",
        discord_client_id="", discord_client_secret="",
    )
    assert not settings.discord_fake and not settings.discord_enabled
    assert settings.secure_cookies


# -- per-account verdicts -------------------------------------------------------


def test_verdicts_belong_to_the_account(make_client):
    client = make_client()
    register(client)
    call = lambda c, m, *a: c.post(f"/api/call/{m}", json={"args": list(a)}, headers=csrf(c)).json()  # noqa: E731
    call(client, "sandbox_judge", "Grizzly Bears", "inert", "test")
    assert call(client, "sandbox_inspect", "Grizzly Bears")["suppressed"]

    stranger = make_client()
    assert not call(stranger, "sandbox_inspect", "Grizzly Bears")["suppressed"]

    # Same account, another browser: the verdict follows the account.
    again = make_client()
    post(again, "/auth/login", {"email": "ana@example.com", "password": "correct horse battery"})
    assert call(again, "sandbox_inspect", "Grizzly Bears")["suppressed"]
