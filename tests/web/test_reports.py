"""Reports: /api/reports, the admin review list, export and deletion."""
# The fixtures are imported from test_library, so the test parameters named
# after them read as redefinitions to ruff.
# ruff: noqa: F811

from __future__ import annotations

import pytest

from mtgfish.web.reports import BadReport, validate

from .test_library import headers, make_client, post, signed_up  # noqa: F401 - fixtures

CARD = {
    "kind": "card", "problem": "missing", "text": "It should draw a card when it enters.",
    "page": "replay", "oracle_id": "abc", "card_name": "Elvish Visionary",
    "card_hash": "h1", "ir_build": "ir-1", "reading": "Has nothing.",
}
STEP = {
    "kind": "step", "text": "The Bears should have died here.", "page": "replay",
    "run_kind": "browser", "run_id": "r1", "game_index": 3, "seed": 99, "log_index": 120,
    "engine_build": "engine-1", "ir_build": "ir-1", "context": '{"decks":[]}',
}
PROBLEM = {"kind": "problem", "text": "The Results page is blank on my phone.", "page": "results"}


def send(client, report):
    return post(client, "/api/reports", report)


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("MTGFISH_ADMIN_EMAILS", "boss@example.com")


# -- what a report must hold ---------------------------------------------------


def test_each_kind_validates_with_what_it_needs():
    assert validate(CARD)["card_name"] == "Elvish Visionary"
    assert validate(STEP)["log_index"] == 120
    assert validate(PROBLEM)["page"] == "results"


@pytest.mark.parametrize("change, message", [
    ({"kind": "rant"}, "kind must be"),
    ({"text": "   "}, "say what's wrong"),
    ({"text": "x" * 4001}, "at most 4000"),
    ({"problem": "ugly"}, "problem must be"),
    ({"card_name": ""}, "needs the card"),
    ({"run_kind": "phone"}, "run_kind"),
])
def test_a_bad_card_report_is_refused(change, message):
    with pytest.raises(BadReport, match=message):
        validate(CARD | change)


def test_a_step_report_needs_the_game_and_the_step():
    with pytest.raises(BadReport, match="needs the game and the step"):
        validate(STEP | {"log_index": None})
    with pytest.raises(BadReport, match="whole number"):
        validate(STEP | {"game_index": -1})


# -- sending ------------------------------------------------------------------


def test_anyone_can_send_a_report_linked_to_no_one(make_client):
    from mtgfish.web.models import Report

    client = make_client()
    reply = send(client, PROBLEM)
    assert reply.status_code == 201 and reply.json()["signed_in"] is False
    with client.accounts.db.session() as db:
        row = db.get(Report, reply.json()["id"])
        assert row.user_id is None and row.status == "new" and row.page == "results"


def test_a_report_needs_the_pages_csrf_token(make_client):
    client = make_client()
    client.get("/auth/me")
    reply = client.post("/api/reports", json=PROBLEM)
    assert reply.status_code == 403


def test_reports_are_rate_limited(make_client):
    client = make_client()
    codes = [send(client, PROBLEM).status_code for _ in range(6)]
    assert codes[:5] == [201] * 5 and codes[5] == 429


def test_your_reports_are_yours(make_client):
    client = signed_up(make_client, "reporter@example.com")
    assert send(client, CARD).json()["signed_in"] is True
    send(client, STEP)
    mine = client.get("/api/reports/mine").json()["reports"]
    assert {r["kind"] for r in mine} == {"card", "step"}
    step = next(r for r in mine if r["kind"] == "step")
    assert (step["run_id"], step["game_index"], step["seed"], step["log_index"]) == ("r1", 3, 99, 120)
    assert make_client().get("/api/reports/mine").status_code == 401


# -- the review list ------------------------------------------------------------


def test_only_admins_see_the_review_list(make_client, admin_env):
    reporter = make_client()
    send(reporter, CARD)
    plain = signed_up(make_client, "someone@example.com")
    assert plain.get("/api/admin/reports").status_code == 403
    assert plain.get("/auth/me").json()["admin"] is False

    boss = signed_up(make_client, "boss@example.com")
    assert boss.get("/auth/me").json()["admin"] is True
    listed = boss.get("/api/admin/reports").json()
    assert [r["card_name"] for r in listed["reports"]] == ["Elvish Visionary"]
    assert listed["counts"]["new"] == 1


def test_an_unverified_admin_email_is_not_an_admin(make_client, admin_env):
    boss = signed_up(make_client, "boss@example.com", verified=False)
    assert boss.get("/api/admin/reports").status_code == 403


def test_an_admin_sets_a_reports_status_and_note(make_client, admin_env):
    reporter = signed_up(make_client, "who@example.com")
    report_id = send(reporter, CARD).json()["id"]
    boss = signed_up(make_client, "boss@example.com")
    reply = post(boss, f"/api/admin/reports/{report_id}", {"status": "confirmed", "note": "F70"})
    assert reply.status_code == 200 and reply.json()["report"]["status"] == "confirmed"
    assert boss.get("/api/admin/reports?status=confirmed").json()["reports"][0]["note"] == "F70"
    assert boss.get("/api/admin/reports?status=new").json()["reports"] == []
    # The reporter sees where it went.
    assert reporter.get("/api/reports/mine").json()["reports"][0]["status"] == "confirmed"
    assert post(boss, f"/api/admin/reports/{report_id}", {"status": "maybe"}).status_code == 400
    assert post(reporter, f"/api/admin/reports/{report_id}", {"status": "fixed"}).status_code == 403


# -- privacy: export, deletion, expiry ----------------------------------------------


def test_the_export_includes_reports(make_client):
    client = signed_up(make_client, "export@example.com")
    send(client, PROBLEM)
    data = client.get("/auth/account/export").json()
    assert [r["text"] for r in data["reports"]] == [PROBLEM["text"]]


def test_deleting_the_account_deletes_its_reports(make_client):
    from mtgfish.web.models import Report

    client = signed_up(make_client, "gone@example.com")
    report_id = send(client, PROBLEM).json()["id"]
    assert post(client, "/auth/account/delete", {"confirm": "DELETE"}).status_code == 200
    with client.accounts.db.session() as db:
        assert db.get(Report, report_id) is None


def test_anonymous_reports_expire(make_client):
    import datetime as dt

    from mtgfish.web.auth.service import purge_expired
    from mtgfish.web.models import Report

    client = make_client()
    report_id = send(client, PROBLEM).json()["id"]
    with client.accounts.db.session() as db:
        db.get(Report, report_id).created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
        db.commit()
        assert purge_expired(db)["anonymous_reports"] == 1
        assert db.get(Report, report_id) is None
