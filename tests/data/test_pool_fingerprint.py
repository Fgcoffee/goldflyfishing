"""Everyone building their own card database must end up with the same one.

The Scryfall dumps are in the repository; the 95 MB database built from them is
not. That only works if every build of those dumps produces the same pool, so
the build records a fingerprint next to them and this checks it. Without it, an
agent quietly running a month-old pool would produce numbers nobody could
reproduce and nothing would say why.
"""

from __future__ import annotations

import json

import pytest

from mtgfish.bootstrap import pool_is_stale
from mtgfish.paths import card_db_path, data_root

REBUILD = "python -m mtgfish.tools.fetch_scryfall --offline"


def _fingerprint() -> dict:
    path = data_root() / "pool.json"
    if not path.exists():
        pytest.skip(f"no pool fingerprint at {path}; write one with {REBUILD}")
    return json.loads(path.read_text(encoding="utf8"))


def test_the_database_was_built_from_the_committed_dumps(card_db):
    expected = _fingerprint()
    if pool_is_stale(card_db_path()):
        # The fixture rebuilds a stale database; reaching here means it could
        # not - on Windows the file cannot be replaced while a server holds it
        # open. An environment problem, and it names its own fix.
        pytest.skip(f"this machine's database is older than the dumps; rebuild with {REBUILD}")
    assert card_db.content_hash == expected["oracle_hash"], (
        f"built from different dumps than the ones committed; rebuild with {REBUILD}"
    )


def test_the_pool_has_the_cards_the_fingerprint_promises(card_db):
    expected = _fingerprint()
    if pool_is_stale(card_db_path()):
        pytest.skip(f"this machine's database is older than the dumps; rebuild with {REBUILD}")
    assert card_db.card_count == expected["cards"]


def test_a_refreshed_dump_marks_the_database_stale(tmp_path, monkeypatch):
    """The check that makes a refresh reach every machine by itself."""
    import sqlite3

    from mtgfish.data import scryfall

    database = tmp_path / "cards.sqlite"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    older = "oracle_cards-20260810T090154.015_0000.jsonl.gz"
    newer = "oracle_cards-20260916T090153.684_0000.jsonl.gz"
    conn.execute("INSERT INTO meta VALUES ('oracle_file', ?)", (older,))
    conn.commit()
    conn.close()

    dumps = tmp_path / "scryfall"
    dumps.mkdir()
    monkeypatch.setattr(scryfall, "scryfall_cache", lambda: dumps)

    (dumps / older).write_bytes(b"")
    assert pool_is_stale(database) is False, "built from the dump that is here"

    # Scryfall stamps each dump with its publication time, so the newest name
    # sorts last - which is how the cache knows which one is current.
    (dumps / newer).write_bytes(b"")
    assert pool_is_stale(database) is True, "a refreshed dump means rebuild"
