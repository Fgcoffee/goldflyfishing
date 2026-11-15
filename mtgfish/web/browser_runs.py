"""Runs played in the browser, sent back to the server: ``/api/browser-runs``.

The TypeScript engine plays games in the visitor's browser. Each run is
sent back here and stored (``models.BrowserRun``), with everything needed to
replay any game: the run seed, each game's seed and log digest, the engine
and IR builds, the card snapshot, and the decks as resolved.

=====================================  =====================================
``POST /api/browser-runs``             store one run (gzipped JSON body)
``GET  /api/browser-runs``             your stored runs (signed in), each
                                       with its deck label and win rate
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
MAX_LABEL = 200
#: The hero's decklist as typed or fetched, used only to name the run.
MAX_DECKLIST = 100_000
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

    deck_name = data.get("deck_name", "")
    hero_decklist = data.get("hero_decklist", "")
    return {
        "deck_name": _text(deck_name, "deck_name", MAX_LABEL).strip(),
        "hero_decklist": _text(hero_decklist, "hero_decklist", MAX_DECKLIST),
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
        "label": run.label or "",
        "win_rate": run.win_rate,
    }


def hero_win_rate(records: list[dict], hero: int) -> float | None:
    """Wins over recorded games, crashed ones left out: the report's
    ``winRate`` (engine/src/sim/report.ts). None with no recorded game."""
    if not records:
        return None
    return sum(1 for r in records if r.get("winner") == hero) / len(records)


def run_label(db, user_id: str | None, deck_name: str, hero_decklist: str,
              commanders: list[str], ir_store) -> str:
    """What History calls the run, as a server run is named
    (``library.record_run``): the saved deck and version holding this list,
    else the Archidekt deck's own name, else the commander."""
    from .library import commander_of, version_for_text
    from .models import Deck

    if user_id is not None and hero_decklist:
        version = version_for_text(db, user_id, hero_decklist)
        deck = db.get(Deck, version.deck_id) if version else None
        if deck is not None and version is not None:
            return f"{deck.name} v{version.number}"[:MAX_LABEL]
    if deck_name:
        return deck_name[:MAX_LABEL]
    named = commander_of(hero_decklist) if hero_decklist else None
    if named:
        return named[:MAX_LABEL]
    names = [card_name(ir_store, oracle_id) for oracle_id in commanders]
    return (" + ".join(n for n in names if n) or "Unsaved deck")[:MAX_LABEL]


def card_name(ir_store, oracle_id: str) -> str:
    """A card's name from the IR export, or "" if it isn't there."""
    path = ir_store.card_path(oracle_id)
    if path is None:
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("name", "")
    except (OSError, ValueError):
        return ""


def fill_listing(db, run: BrowserRun, ir_store) -> bool:
    """Give a run stored before labels were kept its label and win rate,
    from its decks and games. Returns whether anything changed."""
    changed = False
    if run.win_rate is None:
        games = json.loads(gzip.decompress(run.payload))
        rate = hero_win_rate(games["records"], run.hero)
        if rate is not None:
            run.win_rate = rate
            changed = True
    if not run.label:
        decks = json.loads(run.decks)
        hero = decks[run.hero] if 0 <= run.hero < len(decks) else {}
        run.label = run_label(db, None, "", "", hero.get("commanders", []), ir_store)
        changed = True
    return changed


def build_router(accounts, limiter: RateLimiter | None = None, ir_store=None) -> APIRouter:
    router = APIRouter(prefix="/api")
    if ir_store is None:
        from .ir import IrStore

        # Only card_path is used, which reads the export directory.
        ir_store = IrStore()
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
                label=run_label(
                    db, user.id if user else None, run["deck_name"], run["hero_decklist"],
                    run["decks"][run["hero"]]["commanders"], ir_store,
                ),
                win_rate=hero_win_rate(run["records"], run["hero"]),
            )
            db.add(row)
            db.commit()
            log.info("stored browser run %s: %d games, %d crashed, %d bytes",
                     row.id, row.games, row.crashed, len(payload))
            return reply({"id": row.id, "games": row.games, "crashed": row.crashed,
                          "spot_check": row.spot_check, "signed_in": user is not None}, 201)

    @router.get("/browser-runs/{run_id}")
    def open_run(run_id: str, request: Request):
        """One of your runs, whole, for the Results and Replay screens.

        ``setup`` is what the browser engine needs to replay any game: the
        decks and each card's IR as exported now. When a card has changed
        since the run, the games can't be replayed as they were played, so
        ``setup`` is null and ``unverifiable`` says which card; the results
        themselves still stand.
        """
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if user is None:
                return reply({"error": "log in to see your runs"}, 401)
            run = db.scalars(
                select(BrowserRun).where(BrowserRun.id == run_id, BrowserRun.user_id == user.id)
            ).one_or_none()
            if run is None:
                return reply({"error": "no such run"}, 404)
            games = json.loads(gzip.decompress(run.payload))
            setup, unverifiable = None, ""
            try:
                setup = run_setup(run, exported_cards(ir_store, json.loads(run.decks)))
            except Unverifiable as exc:
                unverifiable = str(exc)
            return reply({
                "run": run_row(run),
                "records": games["records"],
                "crashed": games["crashed"],
                "setup": setup,
                "unverifiable": unverifiable,
            })

    @router.get("/browser-runs")
    def mine(request: Request):
        with database.session() as db:
            user = user_for_session(db, request.cookies.get(AUTH_COOKIE))
            if user is None:
                return reply({"error": "log in to see your runs"}, 401)
            rows = list(db.scalars(
                select(BrowserRun).where(BrowserRun.user_id == user.id)
                .order_by(BrowserRun.created_at.desc()).limit(200)
            ))
            # Runs stored before labels were kept get theirs now, once.
            if any([fill_listing(db, r, ir_store) for r in rows]):
                db.commit()
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


# ---------------------------------------------------------------------------
# The spot check: replay a sample of each pending run in Node
# ---------------------------------------------------------------------------
#
# A run is "pending" when stored. The check rebuilds the run's setup from the
# exported card IR (each card's hash must still be the one the run recorded),
# replays a deterministic sample of its games with the engine's Node CLI
# (engine/dist/replay.cjs, ``npm run build:replay``), and compares digests:
#
# - "ok": every sampled game replayed to its uploaded digest (and a game that
#   crashed in the browser crashed again). Permanent: the build is recorded.
# - "mismatch": a digest differed - a tampered or broken client, or an engine
#   that reads unordered state. Kept out of any shared statistics.
# - "unverifiable": the server can't replay it as played - a card's IR has
#   changed since, or the run is from another engine build. Also kept out.

#: Games replayed per run. Enough to catch a client that fakes results
#: wholesale; one game is a fraction of a second.
SAMPLE_GAMES = 8
#: Seconds the replay of one run's sample may take.
REPLAY_TIMEOUT = 300
#: Pending runs looked at per pass of the background job.
RUNS_PER_PASS = 20

REPLAY_CLI = engine_build.ENGINE_SRC.parent / "dist" / "replay.cjs"


class Unverifiable(Exception):
    """The run can't be replayed as it was played."""


def node_binary() -> str | None:
    import os
    import shutil

    return os.environ.get("MTGFISH_NODE") or shutil.which("node")


def replay_cli() -> "Path":
    import os
    from pathlib import Path

    return Path(os.environ.get("MTGFISH_REPLAY_CLI") or REPLAY_CLI)


def node_replayer(request: dict) -> list[dict]:
    """Run the engine's replay CLI on ``request``; its per-game results."""
    import subprocess

    node = node_binary()
    cli = replay_cli()
    if node is None or not cli.is_file():
        raise Unverifiable("no Node replay CLI on this server")
    done = subprocess.run(
        [node, str(cli)], input=json.dumps(request).encode(), capture_output=True,
        timeout=REPLAY_TIMEOUT, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"replay CLI failed ({done.returncode}): {done.stderr.decode()[:500]}")
    return json.loads(done.stdout)["results"]


def exported_cards(ir_store, decks: list[dict]) -> dict:
    """Each card the decks name, as exported now - refused if its hash changed."""
    cards: dict[str, dict] = {}
    wanted: dict[str, str | None] = {}
    for deck in decks:
        for oracle_id in deck.get("commanders", []):
            wanted.setdefault(oracle_id, None)
        for entry in deck.get("cards", []):
            wanted[entry["oracle_id"]] = entry.get("card_hash")
    for oracle_id, card_hash in wanted.items():
        path = ir_store.card_path(oracle_id)
        if path is None:
            raise Unverifiable(f"card {oracle_id} is not in this server's IR export")
        document = json.loads(path.read_text(encoding="utf-8"))
        if card_hash is not None and document.get("card_hash") != card_hash:
            raise Unverifiable(f"card {document.get('name', oracle_id)} has changed since the run")
        cards[oracle_id] = document
    return cards


def run_setup(run: BrowserRun, cards: dict) -> dict:
    """The engine's RunSetup for a stored run (engine/src/sim/runner.ts)."""
    decks = json.loads(run.decks)
    return {
        "decks": [{"commanders": deck.get("commanders", []),
                   "cards": [{"oracle_id": e["oracle_id"], "count": e["count"]}
                             for e in deck.get("cards", [])]} for deck in decks],
        "cards": cards,
        "runSeed": run.run_seed,
        "maxRounds": run.max_rounds,
    }


def sample(run_id: str, games: list[dict], count: int = SAMPLE_GAMES) -> list[dict]:
    """A deterministic sample of a run's games (the same each time it's checked)."""
    import random

    ordered = sorted(games, key=lambda game: game["index"])
    if len(ordered) <= count:
        return ordered
    return sorted(random.Random(run_id).sample(ordered, count), key=lambda game: game["index"])


def check_run(run: BrowserRun, ir_store, replayer=node_replayer) -> tuple[str, str]:
    """Replay a sample of one run: ("ok" | "mismatch" | "unverifiable", detail)."""
    server_build = engine_build.current()
    if server_build is not None and run.engine_build != server_build:
        return "unverifiable", f"engine build {run.engine_build} is not this server's ({server_build})"
    games = json.loads(gzip.decompress(run.payload))
    decks = json.loads(run.decks)
    try:
        cards = exported_cards(ir_store, decks)
    except Unverifiable as exc:
        return "unverifiable", str(exc)
    expected = {g["index"]: ("digest", g["digest"]) for g in games["records"]}
    expected |= {g["index"]: ("error", None) for g in games["crashed"]}
    chosen = sample(run.id, games["records"] + games["crashed"])
    request = {
        "setup": run_setup(run, cards),
        "games": [{"index": g["index"], "seed": g["seed"]} for g in chosen],
    }
    try:
        results = replayer(request)
    except Unverifiable as exc:
        return "unverifiable", str(exc)
    differ = []
    for result in results:
        kind, digest = expected[result["index"]]
        if kind == "digest" and result.get("digest") != digest:
            differ.append(f"game {result['index']}: {digest} uploaded, "
                          f"{result.get('digest') or result.get('error')} replayed")
        elif kind == "error" and "error" not in result:
            differ.append(f"game {result['index']}: crashed in the browser, not on replay")
    if differ:
        return "mismatch", "; ".join(differ[:5])
    return "ok", f"{len(results)} game(s) replayed to their digests"


def check_pending(database, ir_store, replayer=node_replayer, limit: int = RUNS_PER_PASS) -> int:
    """Spot-check up to ``limit`` pending runs, oldest first. Returns how many."""
    with database.session() as db:
        ids = list(db.scalars(
            select(BrowserRun.id).where(BrowserRun.spot_check == "pending")
            .order_by(BrowserRun.created_at).limit(limit)
        ))
    for run_id in ids:
        with database.session() as db:
            run = db.get(BrowserRun, run_id)
            if run is None or run.spot_check != "pending":
                continue
            try:
                verdict, detail = check_run(run, ir_store, replayer)
            except Exception as exc:  # noqa: BLE001 - stays pending, tried next pass
                log.warning("spot check of run %s failed: %s", run_id, exc)
                continue
            run.spot_check = verdict
            run.spot_check_detail = detail[:2000]
            db.commit()
            log.info("spot check of run %s: %s (%s)", run_id, verdict, detail)
    return len(ids)
