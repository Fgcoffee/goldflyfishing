"""First-run setup for the packaged application.

The card database is a 99 MB SQLite file. It cannot live inside the executable
folder and be written to - a normal user has no write access to Program Files -
and it must not be rebuilt from Scryfall on every launch either.

So the build ships it as a read-only asset beside the executable, and the first
launch copies it into the per-user data directory, where the rest of the
program already expects to find it (see ``paths.data_root``).

The copy is deliberate rather than a symlink or a direct read: the running
program writes to this database (metadata, and a rebuild when the card pool is
refreshed), and writing into the install directory is what breaks a packaged
Windows application for everyone except the person who installed it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .paths import card_db_path, data_root, ensure


def bundled_root() -> Path | None:
    """Where PyInstaller unpacked the read-only assets, if we are packaged."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return None


def pool_is_stale(target: Path) -> bool:
    """Whether the dumps in the cache describe a newer pool than this database.

    The dumps are in the repository and the database is not, so refreshing the
    pool is a commit that changes a file nobody's machine has rebuilt from yet.
    Answered by the dump filename the database recorded when it was built:
    Scryfall stamps each one with its publication time, so a mismatch means
    exactly "built from a different dump than the one sitting here".
    """
    import sqlite3

    from .data.scryfall import ScryfallClient

    newest = ScryfallClient(offline=True).cached_bulk("oracle_cards")
    if newest is None:
        return False  # Nothing to rebuild from; what is there is what there is.
    from .data.db import SCHEMA_VERSION

    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'oracle_file'"
            ).fetchone()
            schema = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return True  # Unreadable or half-written: rebuilding is the answer.
    # A database built by an older schema is missing columns the code now
    # reads, so it is as stale as one built from an older dump.
    if not schema or schema[0] != str(SCHEMA_VERSION):
        return True
    return not row or row[0] != newest.name


def build_from_dumps(*, report=None) -> Path | None:
    """Build the card database from the dumps in the cache, offline.

    Returns None when there are no dumps to build from - a packaged build, or
    a checkout whose cache was cleared - so the caller can report a missing
    pool rather than failing later with no cards at all.
    """
    from .data.scryfall import ScryfallClient
    from .paths import scryfall_cache

    client = ScryfallClient(offline=True)
    if client.cached_bulk("oracle_cards") is None:
        return None
    if report is not None:
        report(f"Building the card database from {scryfall_cache()} (first run only)...")
    from .data.db import build_database

    return build_database(client=client, progress=report)


def seed_card_database(*, report=None) -> Path:
    """Make sure a card database exists, and return where it is.

    Returns the path either way, so the caller can report a missing database
    rather than failing later with an empty pool - a simulator that silently
    runs with no cards would report a perfectly clean zero percent win rate.
    """
    target = card_db_path()
    if target.exists() and target.stat().st_size > 0:
        if not pool_is_stale(target):
            return target
        # Refreshed dumps: rebuild so everyone working from this checkout is
        # measuring the same card pool. Not fatal if it cannot be done now -
        # on Windows the file cannot be replaced while a server holds it open -
        # because yesterday's pool beats no pool at all.
        try:
            return build_from_dumps(report=report) or target
        except OSError as exc:
            if report is not None:
                report(
                    f"The card pool has been refreshed but {target} is in use "
                    f"({exc.__class__.__name__}); keeping the existing one. "
                    "Rebuild with: python -m mtgfish.tools.fetch_scryfall --offline"
                )
            return target

    bundle = bundled_root()
    if bundle is None:
        # A clone carries Scryfall's dumps but not the 95 MB database built
        # from them, so build it here rather than making every user - and
        # every agent working in the repository - know to run a command first.
        # It takes a few seconds and needs no network.
        return build_from_dumps(report=report) or target

    source = bundle / "cards.sqlite"
    if not source.exists():
        return target

    ensure(data_root())
    if report is not None:
        report("Preparing the card database (first run only)...")
    shutil.copy2(source, target)
    return target
