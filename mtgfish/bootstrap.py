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


def seed_card_database(*, report=None) -> Path:
    """Make sure a card database exists, and return where it is.

    Returns the path either way, so the caller can report a missing database
    rather than failing later with an empty pool - a simulator that silently
    runs with no cards would report a perfectly clean zero percent win rate.
    """
    target = card_db_path()
    if target.exists() and target.stat().st_size > 0:
        return target

    bundle = bundled_root()
    if bundle is None:
        return target  # Development: the cache directory is the source of truth.

    source = bundle / "cards.sqlite"
    if not source.exists():
        return target

    ensure(data_root())
    if report is not None:
        report("Preparing the card database (first run only)...")
    shutil.copy2(source, target)
    return target
