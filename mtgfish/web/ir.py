"""Card IR for the browser engine: the /api/ir and /ir/v<N> routes.

The TypeScript engine reads each card's abilities as JSON IR (see
``schema/README.md``) produced by ``python -m mtgfish.tools.ir_export export``.
The browser never gets the whole pool or the 7 MB name index: it sends its
decklists, the server resolves them, and the browser fetches only those
cards' files.

=====================================  =====================================
``GET  /api/ir``                       is IR available; version, snapshot
``POST /api/ir/decks``                 {"decklists": [...]} -> each deck's
                                       cards by oracle id, and every card's
                                       hash and file URL (or the document
                                       itself, when parsed live)
``GET  /ir/v<N>/<oracle_id>.json``     one exported card document (N:
                                       the parser's ``IR_VERSION``)
=====================================  =====================================

Cards come through the parser's ``mtgfish.ir.store.IRStore``: the exported
file when it is current, else a live parse. A live parse should be rare (the
image exports from the same code and card data it serves); each one is
logged as a warning, and its document is sent inline, never as a cacheable
file.

An exported card's URL carries its hash and the export's build fingerprint
(``?h=&b=``). When both match the current export the file is served as
immutable, so the browser's HTTP cache keeps it until either changes; any
other request is served ``no-cache`` with an ETag.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..ir.codec import IR_VERSION
from . import engine_build
from .auth.ratelimit import RateLimited, RateLimiter

log = logging.getLogger("mtgfish.web.ir")

MAX_DECKS = 4
MAX_DECKLIST = 60_000
_ORACLE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

LIMITS = {
    "ir_decks": (60, 60),     # deck resolutions per address per minute
    "ir_card": (3000, 60),    # card files per address per minute (a pod is ~400)
}


def default_ir_dir() -> Path:
    import os

    from ..paths import data_root

    override = os.environ.get("MTGFISH_IR_DIR")
    return Path(override) if override else data_root() / "ir" / f"v{IR_VERSION}"


def build_fingerprint(index: dict) -> str:
    """What identifies the code that produced an export.

    ``card_hash`` covers a card's text, not the parser: after a parser change
    the same hash can map to different IR. So cached card URLs also carry
    this: the exporter's ``code`` (``mtgfish.ir.store.code_fingerprint``),
    shortened to 16 hex digits for the URL. An older export without it
    falls back to ``built_at``, which changes with every export, so a new
    export always gets new URLs (over-invalidating, never stale).
    """
    code = index.get("code") or index.get("build")
    if code:
        return str(code)[:16]
    return str(index.get("built_at") or "")


class IrStore:
    """The exported IR on disk, plus a card database to resolve decklists.

    The card database's sqlite connection belongs to one thread, so every
    resolution runs on this store's own single worker thread.
    """

    def __init__(self, root: Path | None = None, db_factory=None) -> None:
        self.root = root or default_ir_dir()
        self._db_factory = db_factory
        self._db = None
        self._reader = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ir-db")
        self._lock = threading.Lock()
        self._index: dict | None = None
        self._index_mtime: int | None = None

    # -- the index ------------------------------------------------------------

    def index(self) -> dict | None:
        """``{"snapshot", "built_at", "cards": {oracle_id: card_hash}}``, or None.

        Reloaded when the file changes, so a re-export is picked up without
        a restart. The 7 MB name map is dropped after loading: names are
        resolved through the card database, which knows every spelling a
        decklist uses.
        """
        path = self.root / "index.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        with self._lock:
            if self._index is None or self._index_mtime != mtime:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("ir_version") != IR_VERSION:
                    log.error("IR index at %s is version %r, not %d", path,
                              raw.get("ir_version"), IR_VERSION)
                    return None
                self._index = {
                    "snapshot": raw.get("snapshot", ""),
                    "built_at": raw.get("built_at", ""),
                    "build": build_fingerprint(raw),
                    "cards": {oid: row["card_hash"] for oid, row in raw["cards"].items()},
                }
                self._index_mtime = mtime
            return self._index

    def card_path(self, oracle_id: str) -> Path | None:
        if not _ORACLE_ID.match(oracle_id):
            return None
        path = self.root / f"{oracle_id}.json"
        return path if path.is_file() else None

    # -- decks ----------------------------------------------------------------

    async def resolve(self, decklists: list[str]) -> dict:
        return await asyncio.wrap_future(self._pool.submit(self._resolve, decklists))

    def _database(self):
        if self._db is None:
            if self._db_factory is not None:
                self._db = self._db_factory()
            else:
                from ..data.db import CardDatabase

                self._db = CardDatabase()
                self._db.registry()
        return self._db

    def _ir_store(self):
        """The parser's reader (``mtgfish.ir.store``), on this thread's database."""
        if self._reader is None:
            from ..ir.store import IRStore

            self._reader = IRStore(self._database(), self.root)
        return self._reader

    def _resolve(self, decklists: list[str]) -> dict:
        from ..data.decks import parse_decklist

        index = self.index() or {"cards": {}, "snapshot": "", "build": ""}
        build = index["build"]
        db = self._database()
        reader = self._ir_store()
        decks, cards = [], {}
        live_now: list[str] = []
        for number, text in enumerate(decklists):
            deck = parse_decklist(text, db, name=f"deck {number + 1}")
            live: list[str] = []

            def take(card) -> str:
                entry = cards.get(card.oracle_id)
                if entry is None:
                    before = reader.live_parses
                    document = reader.card(card)
                    if reader.live_parses == before:
                        # Exported and current: the browser fetches the file,
                        # and may cache it for good under this hash and build.
                        digest = document["card_hash"]
                        entry = {
                            "name": card.name,
                            "card_hash": digest,
                            "url": f"/ir/v{IR_VERSION}/{card.oracle_id}.json"
                                   f"?h={digest}&b={quote(build, safe='')}",
                        }
                    else:
                        # Missing from the export or exported from older card
                        # text, so parsed just now. Sent inline and never
                        # cached: it is not a file the server can serve again.
                        entry = {"name": card.name, "card_hash": document["card_hash"],
                                 "document": document}
                        live_now.append(card.name)
                    cards[card.oracle_id] = entry
                if "document" in entry and card.name not in live:
                    live.append(card.name)
                return card.oracle_id

            decks.append({
                "commanders": [take(card) for card in deck.commanders],
                "cards": [
                    {"oracle_id": take(entry.card), "name": entry.card.name,
                     "count": entry.quantity}
                    for entry in deck.entries
                ],
                "unresolved": list(deck.unresolved),
                "live_ir": live,
                "issues": [str(issue) for issue in deck.issues],
            })
        if live_now:
            # In production this should never happen: the image exports the IR
            # from the same code and card data it serves. If it shows up in
            # the logs, the export is stale and every one of these costs CPU.
            log.warning(
                "parsed %d card(s) live for a deck request (%d since start): %s",
                len(live_now), reader.live_parses, ", ".join(live_now[:10]),
            )
        return {
            "ir_version": IR_VERSION,
            "snapshot": index["snapshot"],
            "build": build,
            "decks": decks,
            "cards": cards,
        }

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _json(status: int, payload) -> Response:
    return Response(
        json.dumps(payload).encode(), status_code=status,
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def build_router(store: IrStore | None = None, limiter: RateLimiter | None = None) -> APIRouter:
    store = store or IrStore()
    limiter = limiter or RateLimiter(LIMITS)
    router = APIRouter()
    router.ir_store = store  # type: ignore[attr-defined] - for tests and shutdown

    @router.get("/api/ir")
    async def status():
        index = await asyncio.to_thread(store.index)
        if index is None:
            return _json(200, {"available": False, "ir_version": IR_VERSION})
        return _json(200, {
            "available": True,
            "ir_version": IR_VERSION,
            "snapshot": index["snapshot"],
            "built_at": index["built_at"],
            "build": index["build"],
            "cards": len(index["cards"]),
            # The engine build browser runs must be played with.
            "engine_build": engine_build.current(),
        })

    @router.post("/api/ir/decks")
    async def decks(request: Request):
        try:
            limiter.hit("ir_decks", _address(request))
        except RateLimited as exc:
            return _json(429, {"error": str(exc), "retry_after": exc.retry_after})
        if "application/json" not in request.headers.get("content-type", ""):
            return _json(415, {"error": "send JSON"})
        raw = await request.body()
        if len(raw) > MAX_DECKS * MAX_DECKLIST + 1000:
            return _json(413, {"error": "request too large"})
        try:
            body = json.loads(raw or b"{}")
            decklists = body.get("decklists") if isinstance(body, dict) else None
            if (
                not isinstance(decklists, list)
                or not 1 <= len(decklists) <= MAX_DECKS
                or not all(isinstance(d, str) and len(d) <= MAX_DECKLIST for d in decklists)
            ):
                raise ValueError(f"decklists must be 1 to {MAX_DECKS} strings")
        except ValueError as exc:
            return _json(400, {"error": f"bad request: {exc}"})
        if await asyncio.to_thread(store.index) is None:
            return _json(503, {"error": "card IR has not been exported on this server"})
        try:
            return _json(200, await store.resolve(decklists))
        except Exception as exc:  # noqa: BLE001 - the page needs to hear about it
            log.exception("resolving decks for IR failed")
            return _json(500, {"error": f"{type(exc).__name__}: {exc}"})

    @router.api_route(f"/ir/v{IR_VERSION}/{{name}}", methods=["GET", "HEAD"])
    async def card(name: str, request: Request):
        try:
            limiter.hit("ir_card", _address(request))
        except RateLimited as exc:
            return _json(429, {"error": str(exc), "retry_after": exc.retry_after})
        oracle_id = name.removesuffix(".json")
        path = store.card_path(oracle_id) if name.endswith(".json") else None
        index = await asyncio.to_thread(store.index)
        digest = index["cards"].get(oracle_id) if index else None
        if path is None or digest is None:
            return _json(404, {"error": "no such card"})

        etag = f'"{digest}-{index["build"]}"'
        # Immutable only under the exact card hash and build the manifest
        # handed out: either one changing gives the card a new URL.
        fresh = (
            request.query_params.get("h") == digest
            and request.query_params.get("b") == index["build"]
        )
        headers = {
            "ETag": etag,
            "Vary": "Accept-Encoding",
            "Cache-Control": "public, max-age=31536000, immutable" if fresh else "no-cache",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        body = await asyncio.to_thread(path.read_bytes)
        if len(body) > 1024 and "gzip" in request.headers.get("accept-encoding", ""):
            body = gzip.compress(body, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
        if request.method == "HEAD":
            return Response(status_code=200, media_type="application/json", headers=headers)
        return Response(body, media_type="application/json; charset=utf-8", headers=headers)

    return router
