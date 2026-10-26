"""The parser's output for every card, computed ahead of time.

Parsing a card takes a few milliseconds, and every process that plays a game
parses each card it meets again: a 400-card pool costs about three seconds
before the first turn. The store holds every face's abilities and failures,
read back in microseconds.

It is a cache of the parser, never a second source of truth. Each entry is
served only while three things still match what it was built from:

* the code that produces it - every module under ``mtgfish/parser`` and
  ``mtgfish/rules`` (the keyword builders and the IR live in the rules
  package) and the card definitions;
* the card data - the Scryfall snapshot, and the subtype and keyword catalogs
  that decide whether "Goblins" is a creature type;
* the card itself - a hash of the whole card definition, so a card whose
  Scryfall text changed is parsed afresh.

When the code or the vocabulary has moved on, the store is *stale* and is not
used at all: every card is parsed live, exactly as without a store, until
``python -m mtgfish.tools.parse_store build`` brings it up to date. The git
hooks in ``.githooks`` and the Scryfall fetch run that build when the code or
the data changes, but nothing depends on them having run.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import sqlite3
import threading
import zlib
from pathlib import Path

#: Setting MTGFISH_PARSE_STORE=off parses every card live. The test suite does,
#: because a test that patches the parser must see its patch take effect.
ENV_SWITCH = "MTGFISH_PARSE_STORE"

_PACKAGE = Path(__file__).resolve().parent.parent

#: The source the parser's output depends on.
_CODE_ROOTS = ("parser", "rules")
_CODE_FILES = ("data/cards.py",)


def store_path() -> Path:
    from ..paths import data_root

    return data_root() / "parse_store.sqlite"


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").lower() not in ("off", "0", "false", "no")


_code_fingerprint: str | None = None


def code_fingerprint() -> str:
    """A hash of every source file the parser's output depends on.

    Computed once per process: a process runs the code it imported, whatever
    happens to the files afterwards.
    """
    global _code_fingerprint
    if _code_fingerprint is None:
        digest = hashlib.sha256()
        paths = [p for root in _CODE_ROOTS for p in (_PACKAGE / root).rglob("*.py")]
        paths += [_PACKAGE / name for name in _CODE_FILES]
        for path in sorted(paths):
            digest.update(str(path.relative_to(_PACKAGE)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        _code_fingerprint = digest.hexdigest()
    return _code_fingerprint


def data_fingerprint(db) -> str:
    """A hash of the card data: the Scryfall snapshot the database was built
    from, and the pool-wide vocabulary the grammar consults."""
    digest = hashlib.sha256()
    digest.update(str(getattr(db, "content_hash", "")).encode())
    for table, columns in (("subtypes", "card_type, subtype"), ("vocabulary", "catalog, value")):
        for row in db._conn.execute(f"SELECT {columns} FROM {table} ORDER BY {columns}"):
            digest.update("\t".join(str(v) for v in tuple(row)).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def card_hash(card) -> str:
    """A hash of the whole card definition, every field the parser could read."""
    return hashlib.sha256(repr(card).encode()).hexdigest()[:32]


class ParseStore:
    """Read access to a built store. ``get`` answers only for a fresh store."""

    def __init__(self, path: Path, conn: sqlite3.Connection, meta: dict[str, str]) -> None:
        self.path = path
        self._conn = conn
        self.meta = meta
        self.fresh = False
        # One connection, shared by the engine's threads (the UI resolves on
        # a worker thread); sqlite3 connections are not safe to use at once.
        self._lock = threading.Lock()

    @classmethod
    def open(cls, db, path: Path | None = None) -> ParseStore | None:
        path = path or store_path()
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        except sqlite3.Error:
            return None
        store = cls(path, conn, meta)
        store.fresh = (
            meta.get("code") == code_fingerprint() and meta.get("data") == data_fingerprint(db)
        )
        return store

    def get(self, card, face_index: int):
        """``(abilities, failures)`` for this face, or None to parse it live."""
        if not self.fresh:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT card_hash, blob FROM faces WHERE oracle_id = ? AND face_index = ?",
                (getattr(card, "oracle_id", ""), face_index),
            ).fetchone()
        if row is None or row[0] != card_hash(card):
            return None
        return pickle.loads(zlib.decompress(row[1]))

    def close(self) -> None:
        self._conn.close()


def status(db, path: Path | None = None) -> dict:
    """What a build would find: missing, stale (and why), or fresh."""
    path = path or store_path()
    store = ParseStore.open(db, path)
    if store is None:
        return {"state": "missing", "path": str(path)}
    reasons = []
    if store.meta.get("code") != code_fingerprint():
        reasons.append("parser or rules code changed")
    if store.meta.get("data") != data_fingerprint(db):
        reasons.append("card data changed")
    out = {
        "state": "fresh" if not reasons else "stale",
        "path": str(path),
        "built_at": store.meta.get("built_at", ""),
        "faces": store.meta.get("faces", ""),
        "reasons": reasons,
    }
    store.close()
    return out


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

_WORKER_DB = None


def _worker_init(db_path: str) -> None:
    global _WORKER_DB
    os.environ[ENV_SWITCH] = "off"
    from ..data.db import CardDatabase

    _WORKER_DB = CardDatabase(Path(db_path))
    _WORKER_DB.registry()


def _parse_chunk(oracle_ids: list[str]) -> list[tuple]:
    from .compile import parse_face

    rows = []
    for oracle_id in oracle_ids:
        card = _WORKER_DB.by_oracle_id(oracle_id)
        if card is None:
            continue
        digest = card_hash(card)
        for face_index in range(max(1, len(card.faces))):
            try:
                parsed = parse_face(card, face_index)
            except Exception:  # noqa: BLE001, S112 - a crash is left to the live parse
                continue
            blob = zlib.compress(pickle.dumps(
                (tuple(parsed.abilities), list(parsed.failures)), protocol=pickle.HIGHEST_PROTOCOL
            ))
            rows.append((oracle_id, face_index, digest, blob))
    return rows


def build(
    db,
    path: Path | None = None,
    jobs: int | None = None,
    progress=None,
    oracle_ids: list[str] | None = None,
) -> dict:
    """Parse every card in ``db`` and write the store, replacing any old one.

    Written to a temporary file and renamed into place, so a reader never
    sees half a store and an interrupted build leaves the old one standing.
    """
    import time
    from concurrent.futures import ProcessPoolExecutor

    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    # Taken before any parsing: a source file edited during the build must
    # leave the store stale, not stamp the old parse with the new code.
    code, data = code_fingerprint(), data_fingerprint(db)
    if oracle_ids is None:
        oracle_ids = [card.oracle_id for card in db.iter_cards()]
    chunks = [oracle_ids[i:i + 200] for i in range(0, len(oracle_ids), 200)]

    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE faces (oracle_id TEXT, face_index INTEGER, card_hash TEXT, blob BLOB,"
        " PRIMARY KEY (oracle_id, face_index))"
    )
    faces = 0
    with ProcessPoolExecutor(
        max_workers=jobs or os.cpu_count() or 1,
        initializer=_worker_init,
        initargs=(str(db.path),),
    ) as pool:
        for done, rows in enumerate(pool.map(_parse_chunk, chunks), 1):
            conn.executemany("INSERT OR REPLACE INTO faces VALUES (?, ?, ?, ?)", rows)
            faces += len(rows)
            if progress is not None:
                progress(done, len(chunks))
    meta = {
        "code": code,
        "data": data,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "faces": str(faces),
        "cards": str(len(oracle_ids)),
        "seconds": f"{time.time() - started:.0f}",
    }
    conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    conn.commit()
    conn.close()
    os.replace(tmp, path)
    return meta
