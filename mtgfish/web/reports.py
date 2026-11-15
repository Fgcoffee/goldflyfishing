"""Reports: people tell us where a card is read wrongly, where a game went
wrong, or anything else that's wrong with the site.

=========================================  ==================================
``POST /api/reports``                      send one (anyone; rate limited)
``GET  /api/reports/mine``                 the reports you sent (signed in)
``GET  /api/admin/reports?status=new``     the review list (admins)
``POST /api/admin/reports/{id}``           set a report's status and note
=========================================  ==================================

Three kinds, each stored with what is needed to act on it:

- ``card``: "Report this reading", from a card window. The oracle id and
  name, the card's IR hash and the IR build, the reading the person saw,
  and what's wrong (``missing``, ``wrong``, ``extra`` or ``other``).
- ``step``: "Report this step", from Replay. The run (browser or server),
  game index and seed, the engine and IR builds, the decks, and the step's
  index in the game log, so the game can be replayed to exactly that point.
- ``problem``: the "Report a problem" button on every page. The page, and
  the run or deck in view if any.

Every kind carries the person's own words. Signed in, a report belongs to
the account: it is in the account export and deleted with the account.
Signed out, it is linked to nobody. Admins are the accounts named in
``MTGFISH_ADMIN_EMAILS`` (verified email) or ``MTGFISH_ADMIN_DISCORD_IDS``.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .auth.ratelimit import RateLimited, RateLimiter
from .auth.routes import AUTH_COOKIE, CSRF_COOKIE
from .auth.service import user_for_session
from .models import Report, User, now

log = logging.getLogger("mtgfish.web.reports")

KINDS = ("card", "step", "problem")
STATUSES = ("new", "confirmed", "fixed", "not_a_bug", "duplicate")
#: What's wrong with a card's reading.
CARD_PROBLEMS = ("missing", "wrong", "extra", "other")

MAX_TEXT = 4000
MAX_READING = 20_000
MAX_CONTEXT = 20_000
MAX_SHORT = 200
MAX_LIST = 500

#: Per address: a few a minute, and a cap per day, so a script can't fill
#: the review list. Signed in or not.
LIMITS = {"report_minute": (5, 60), "report_day": (60, 86400)}


class BadReport(ValueError):
    """A report we can't store."""


def _text(data: dict, key: str, limit: int, *, required: bool = False) -> str:
    value = data.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BadReport(f"{key} must be text")
    value = value.strip()
    if len(value) > limit:
        raise BadReport(f"{key} must be at most {limit} characters")
    if required and not value:
        raise BadReport(f"say what's wrong ({key} is empty)")
    return value


def _int(data: dict, key: str) -> int | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**32:
        raise BadReport(f"{key} must be a whole number")
    return value


def validate(data) -> dict:
    """The report, checked and trimmed to what is stored. Raises ``BadReport``."""
    if not isinstance(data, dict):
        raise BadReport("send a JSON object")
    kind = data.get("kind")
    if kind not in KINDS:
        raise BadReport(f"kind must be one of {', '.join(KINDS)}")
    problem = _text(data, "problem", 20)
    if kind == "card" and problem not in CARD_PROBLEMS:
        raise BadReport(f"problem must be one of {', '.join(CARD_PROBLEMS)}")
    run_kind = _text(data, "run_kind", 20)
    if run_kind not in ("", "browser", "server"):
        raise BadReport("run_kind must be browser or server")
    report = {
        "kind": kind,
        "problem": problem,
        "text": _text(data, "text", MAX_TEXT, required=True),
        "page": _text(data, "page", MAX_SHORT),
        "oracle_id": _text(data, "oracle_id", 64),
        "card_name": _text(data, "card_name", MAX_SHORT),
        "card_hash": _text(data, "card_hash", 64),
        "ir_build": _text(data, "ir_build", 80),
        "engine_build": _text(data, "engine_build", 80),
        "reading": _text(data, "reading", MAX_READING),
        "run_kind": run_kind,
        "run_id": _text(data, "run_id", 64),
        "game_index": _int(data, "game_index"),
        "seed": _int(data, "seed"),
        "log_index": _int(data, "log_index"),
        "context": _text(data, "context", MAX_CONTEXT),
    }
    if kind == "card" and not report["card_name"]:
        raise BadReport("a card report needs the card's name")
    if kind == "step" and (report["game_index"] is None or report["log_index"] is None):
        raise BadReport("a step report needs the game and the step")
    return report


def is_admin(settings, user: User | None) -> bool:
    """An account named in the admin settings: a verified email, or a
    Discord id."""
    if user is None:
        return False
    if user.email and user.email_verified and user.email.lower() in settings.admin_emails:
        return True
    return bool(user.discord_id) and user.discord_id in settings.admin_discord_ids


def report_row(report: Report, *, admin: bool = False) -> dict:
    row = {
        "id": report.id,
        "created_at": report.created_at.isoformat(),
        "kind": report.kind,
        "problem": report.problem,
        "text": report.text,
        "page": report.page,
        "oracle_id": report.oracle_id,
        "card_name": report.card_name,
        "card_hash": report.card_hash,
        "ir_build": report.ir_build,
        "engine_build": report.engine_build,
        "reading": report.reading,
        "run_kind": report.run_kind,
        "run_id": report.run_id,
        "game_index": report.game_index,
        "seed": report.seed,
        "log_index": report.log_index,
        "context": report.context,
        "status": report.status,
        "note": report.note,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }
    if admin:
        row["signed_in"] = report.user_id is not None
    return row


def build_router(accounts, limiter: RateLimiter | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")
    database = accounts.db
    settings = accounts.settings
    limiter = limiter or RateLimiter(LIMITS)

    def reply(payload, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    def csrf_ok(request: Request) -> bool:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("x-csrf-token", "")
        return bool(cookie) and secrets.compare_digest(cookie, header)

    async def body(request: Request):
        try:
            return await request.json()
        except ValueError:
            return None

    @router.post("/reports")
    async def send(request: Request):
        address = request.client.host if request.client else "unknown"
        if not csrf_ok(request):
            return reply({"error": "the page is out of date - reload and try again"}, 403)
        if "application/json" not in request.headers.get("content-type", ""):
            return reply({"error": "send JSON"}, 415)
        try:
            limiter.hit("report_minute", address)
            limiter.hit("report_day", address)
        except RateLimited as exc:
            return reply({"error": str(exc), "retry_after": exc.retry_after}, 429)
        try:
            report = validate(await body(request))
        except BadReport as exc:
            return reply({"error": str(exc)}, 400)
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            row = Report(user_id=user.id if user else None, status="new", **report)
            db.add(row)
            db.commit()
            log.info("report %s (%s) from %s", row.id, row.kind, "an account" if user else "anonymous")
            return reply({"id": row.id, "status": row.status, "signed_in": user is not None}, 201)

    @router.get("/reports/mine")
    def mine(request: Request):
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if user is None:
                return reply({"error": "log in to see your reports"}, 401)
            rows = db.scalars(
                select(Report).where(Report.user_id == user.id)
                .order_by(Report.created_at.desc()).limit(MAX_LIST)
            )
            return reply({"reports": [report_row(r) for r in rows]})

    @router.get("/admin/reports")
    def review(request: Request, status: str = "new", kind: str = ""):
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if not is_admin(settings, user):
                return reply({"error": "admins only"}, 403)
            query = select(Report).order_by(Report.created_at.desc()).limit(MAX_LIST)
            if status in STATUSES:
                query = query.where(Report.status == status)
            if kind in KINDS:
                query = query.where(Report.kind == kind)
            counts = {s: 0 for s in STATUSES}
            for value in db.scalars(select(Report.status)):
                counts[value] = counts.get(value, 0) + 1
            return reply({
                "reports": [report_row(r, admin=True) for r in db.scalars(query)],
                "counts": counts,
                "statuses": list(STATUSES),
            })

    @router.post("/admin/reports/{report_id}")
    async def set_status(report_id: str, request: Request):
        if not csrf_ok(request):
            return reply({"error": "the page is out of date - reload and try again"}, 403)
        data = await body(request)
        if not isinstance(data, dict):
            return reply({"error": "send a JSON object"}, 400)
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if not is_admin(settings, user):
                return reply({"error": "admins only"}, 403)
            report = db.get(Report, report_id)
            if report is None:
                return reply({"error": "no such report"}, 404)
            status = data.get("status", report.status)
            if status not in STATUSES:
                return reply({"error": f"status must be one of {', '.join(STATUSES)}"}, 400)
            note = data.get("note", report.note or "")
            if not isinstance(note, str) or len(note) > MAX_TEXT:
                return reply({"error": f"note must be text of at most {MAX_TEXT} characters"}, 400)
            report.status = status
            report.note = note
            report.updated_at = now()
            db.commit()
            return reply({"report": report_row(report, admin=True)})

    return router


def export_reports(db, user_id: str) -> list[dict]:
    """A user's reports, for the account export (GDPR Art. 15/20)."""
    return [report_row(r) for r in db.scalars(select(Report).where(Report.user_id == user_id))]


#: Reports sent without signing in are linked to nobody; kept this long.
ANONYMOUS_DAYS = 365


def purge_anonymous(db, moment) -> int:
    """Delete anonymous reports older than ANONYMOUS_DAYS. Returns how many."""
    import datetime as dt

    from sqlalchemy import delete

    result = db.execute(
        delete(Report).where(
            Report.user_id.is_(None),
            Report.created_at <= moment - dt.timedelta(days=ANONYMOUS_DAYS),
        )
    )
    return result.rowcount or 0

