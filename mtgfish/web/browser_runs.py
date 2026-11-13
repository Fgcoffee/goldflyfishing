"""Runs played in the browser, sent back to the server: ``/api/browser-runs``.

The TypeScript engine plays games in the visitor's browser. Each run is
sent back here and stored (``models.BrowserRun``), with everything needed to
replay any game: the run seed, each game's seed and log digest, the engine
and IR builds, the card snapshot, and the decks as resolved.

=====================================  =====================================
``POST /api/browser-runs``             store one run (gzipped JSON body)
``GET  /api/browser-runs``             your stored runs (signed in)
=====================================  =====================================

A run must come from the engine build this server runs
(``engine_build.current()``); any other is refused with 409 and
``"reload": true``, so the page fetches the new engine and runs again. That
way the spot check always has the right engine to hand, and a run that
passes is "ok" for good, with its build recorded.

A run is also checked on arrival. Every game's seed must be the one
``seed_for(run_seed, index)`` gives (the same function in both engines),
indices must lie in the run's range with none repeated, and sizes are
capped. A run that fails is refused whole. Later, the server replays a
sample in Node and compares digests (``spot_check``). Until then a run is
"pending", and only "ok" runs may feed shared statistics.

Signed in, a run belongs to your account (exported and deleted with it).
Signed out, it's stored with no link to anyone.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import engine_build
from .auth.ratelimit import RateLimited, RateLimiter
from .auth.routes import AUTH_COOKIE
from .auth.service import user_for_session
from .models import BrowserRun

log = logging.getLogger("mtgfish.web.browser_runs")

#: Compressed request body, and what it may expand to (a zip bomb stops here).
MAX_COMPRESSED = 8_000_000
MAX_EXPANDED = 80_000_000
MAX_GAMES = 100_000
MAX_SEATS = 4
MAX_DECK_ENTRIES = 250
MAX_TEXT = 80
#: Anonymous runs are linked to nobody; they are kept this long.
ANONYMOUS_DAYS = 365

LIMITS = {"browser_run": (30, 60)}  # uploads per address per minute


class BadRun(ValueError):
    """An upload that isn't a run we can store."""


def seed_for(run_seed: int, index: int) -> int:
    """The game seed, as ``mtgfish.sim.runner.RunConfig.seed_for`` and the TS
    kernel's ``seedFor`` compute it."""
    from ..sim.runner import RunConfig

    return RunConfig(decklists=(), run_seed=run_seed).seed_for(index)


def expand(raw: bytes, encoding: str) -> bytes:
    """The request body, gunzipped if it says so, never past MAX_EXPANDED."""
    if len(raw) > MAX_COMPRESSED:
        raise BadRun("the run is too large to upload")
    if encoding.strip().lower() != "gzip":
        if len(raw) > MAX_EXPANDED:
            raise BadRun("the run is too large to upload")
        return raw
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(raw, MAX_EXPANDED + 1)
    except zlib.error as exc:
        raise BadRun("the upload is not valid gzip") from exc
    if len(out) > MAX_EXPANDED or inflater.unconsumed_tail:
        raise BadRun("the run is too large once unpacked")
    return out


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadRun(f"{name} must be a whole number from {low} to {high}")
    return value


def _text(value, name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise BadRun(f"{name} must be text of at most {limit} characters")
    return value


def _decks(value) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SEATS:
        raise BadRun(f"decks must list 1 to {MAX_SEATS} seats")
    out = []
    for seat, deck in enumerate(value):
        if not isinstance(deck, dict):
            raise BadRun(f"deck {seat} is not an object")
        commanders = deck.get("commanders", [])
        cards = deck.get("cards")
        if not isinstance(commanders, list) or len(commanders) > 2:
            raise BadRun(f"deck {seat}: commanders must be a list of at most 2")
        if not isinstance(cards, list) or len(cards) > MAX_DECK_ENTRIES:
            raise BadRun(f"deck {seat}: cards must be a list of at most {MAX_DECK_ENTRIES}")
        out.append({
            "commanders": [_text(c, f"deck {seat} commander", 64) for c in commanders],
            "cards": [
                {
                    "oracle_id": _text(entry.get("oracle_id"), f"deck {seat} oracle_id", 64),
                    "card_hash": _text(entry.get("card_hash", ""), f"deck {seat} card_hash", 64),
                    "count": _int(entry.get("count"), f"deck {seat} count", 1, 250),
                }
                if isinstance(entry, dict) else _bad(f"deck {seat}: a card entry is not an object")
                for entry in cards
            ],
        })
    return out


def _bad(message: str):
    raise BadRun(message)


def validate(data) -> dict:
    """The run, checked and trimmed to what is stored. Raises ``BadRun``."""
    if not isinstance(data, dict):
        raise BadRun("send a JSON object")
    run_seed = _int(data.get("run_seed"), "run_seed", 0, 2**32 - 1)
    start = _int(data.get("game_start"), "game_start", 0, MAX_GAMES)
    end = _int(data.get("game_end"), "game_end", start + 1, start + MAX_GAMES)
    decks = _decks(data.get("decks"))
    hero = _int(data.get("hero", 0), "hero", 0, len(decks) - 1)
    complete = data.get("complete")
    if not isinstance(complete, bool):
        raise BadRun("complete must be true or false")

    records = data.get("records")
    crashed = data.get("crashed", [])
    if not isinstance(records, list) or not isinstance(crashed, list):
        raise BadRun("records and crashed must be lists")
    seen: set[int] = set()

    def check_game(game, what: str) -> None:
        if not isinstance(game, dict):
            raise BadRun(f"a {what} is not an object")
        index = _int(game.get("index"), f"{what} index", start, end - 1)
        if index in seen:
            raise BadRun(f"game {index} appears twice")
        seen.add(index)
        if game.get("seed") != seed_for(run_seed, index):
            raise BadRun(f"game {index} does not have its seed for this run seed")

    for record in records:
        check_game(record, "record")
        _text(record.get("digest"), f"game {record['index']} digest", 64)
    for crash in crashed:
        check_game(crash, "crashed game")
        _text(crash.get("error"), f"game {crash['index']} error", 2000)
        _text(crash.get("detail", ""), f"game {crash['index']} detail", 20_000)
    if not records and not crashed:
        raise BadRun("the run has no games")
    if complete and len(seen) != end - start:
        raise BadRun("a complete run must hold every game in its range")

    return {
        "run_seed": run_seed,
        "game_start": start,
        "game_end": end,
        "hero": hero,
        "max_rounds": _int(data.get("max_rounds"), "max_rounds", 1, 1000),
        "complete": complete,
        "engine_build": _text(data.get("engine_build"), "engine_build"),
        "ir_build": _text(data.get("ir_build"), "ir_build"),
        "snapshot": _text(data.get("snapshot"), "snapshot"),
        "decks": decks,
        "records": records,
        "crashed": crashed,
    }


def run_row(run: BrowserRun) -> dict:
    return {
        "id": run.id,
        "created_at": run.created_at.isoformat(),
        "run_seed": run.run_seed,
        "game_start": run.game_start,
        "game_end": run.game_end,
        "games": run.games,
        "crashed": run.crashed,
        "complete": run.complete,
        "hero": run.hero,
        "max_rounds": run.max_rounds,
        "engine_build": run.engine_build,
        "ir_build": run.ir_build,
        "snapshot": run.snapshot,
        "spot_check": run.spot_check,
    }


def build_router(accounts, limiter: RateLimiter | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")
    database = accounts.db
    limiter = limiter or RateLimiter(LIMITS)

    def reply(payload, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    @router.post("/browser-runs")
    async def upload(request: Request):
        address = request.client.host if request.client else "unknown"
        try:
            limiter.hit("browser_run", address)
        except RateLimited as exc:
            return reply({"error": str(exc), "retry_after": exc.retry_after}, 429)
        # A JSON content type can't be sent cross-site without a preflight,
        # which this server never approves (see app.py's /api/call).
        if "application/json" not in request.headers.get("content-type", ""):
            return reply({"error": "send JSON"}, 415)
        raw = await request.body()
        try:
            body = expand(raw, request.headers.get("content-encoding", ""))
            run = validate(json.loads(body))
        except (BadRun, ValueError) as exc:
            message = str(exc) if isinstance(exc, BadRun) else "the upload is not valid JSON"
            return reply({"error": message}, 413 if "too large" in message else 400)

        # Only runs from the engine this server runs are kept, so a sample can
        # always be replayed with the right engine. Older pages reload.
        server_build = engine_build.current()
        if server_build is not None and run["engine_build"] != server_build:
            return reply({
                "error": "the engine has been updated since this page loaded - reload the "
                         "page and run again",
                "reload": True,
                "engine_build": server_build,
            }, 409)

        payload = gzip.compress(
            json.dumps({"records": run["records"], "crashed": run["crashed"]},
                       separators=(",", ":")).encode(),
            compresslevel=6,
        )
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            row = BrowserRun(
                user_id=user.id if user else None,
                run_seed=run["run_seed"],
                game_start=run["game_start"],
                game_end=run["game_end"],
                games=len(run["records"]),
                crashed=len(run["crashed"]),
                complete=run["complete"],
                hero=run["hero"],
                max_rounds=run["max_rounds"],
                engine_build=run["engine_build"],
                ir_build=run["ir_build"],
                snapshot=run["snapshot"],
                decks=json.dumps(run["decks"], separators=(",", ":")),
                payload=payload,
                spot_check="pending",
            )
            db.add(row)
            db.commit()
            log.info("stored browser run %s: %d games, %d crashed, %d bytes",
                     row.id, row.games, row.crashed, len(payload))
            return reply({"id": row.id, "games": row.games, "crashed": row.crashed,
                          "spot_check": row.spot_check, "signed_in": user is not None}, 201)

    @router.get("/browser-runs")
    def mine(request: Request):
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if user is None:
                return reply({"error": "log in to see your runs"}, 401)
            rows = db.scalars(
                select(BrowserRun).where(BrowserRun.user_id == user.id)
                .order_by(BrowserRun.created_at.desc()).limit(200)
            )
            return reply({"runs": [run_row(r) for r in rows]})

    return router


def export_runs(db, user_id: str) -> list[dict]:
    """A user's browser runs in full, for the account export (GDPR Art. 15/20)."""
    out = []
    for run in db.scalars(select(BrowserRun).where(BrowserRun.user_id == user_id)):
        games = json.loads(gzip.decompress(run.payload))
        out.append(run_row(run) | {"decks": json.loads(run.decks)} | games)
    return out


def purge_anonymous(db, moment) -> int:
    """Delete anonymous runs older than ANONYMOUS_DAYS. Returns how many."""
    import datetime as dt

    from sqlalchemy import delete

    result = db.execute(
        delete(BrowserRun).where(
            BrowserRun.user_id.is_(None),
            BrowserRun.created_at <= moment - dt.timedelta(days=ANONYMOUS_DAYS),
        )
    )
    return result.rowcount or 0
