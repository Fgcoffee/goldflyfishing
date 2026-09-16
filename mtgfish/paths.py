"""Where mtgfish keeps things on disk.

Development runs out of the source tree; the packaged executable must not write
next to itself (Program Files is read-only for normal users), so it falls back
to the per-user application data directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def data_root() -> Path:
    """Root for all writable state: caches, card database, simulation runs."""
    override = os.environ.get("MTGFISH_DATA_DIR")
    if override:
        return Path(override)
    if _is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
        return Path(base) / "mtgfish"
    return Path(__file__).resolve().parent.parent / "cache"


def scryfall_cache() -> Path:
    return data_root() / "scryfall"


def card_db_path() -> Path:
    return data_root() / "cards.sqlite"


def runs_dir() -> Path:
    return data_root() / "runs"


def ensure(path: Path) -> Path:
    """Create ``path`` as a directory if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
