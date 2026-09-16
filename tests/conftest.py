"""Shared test fixtures."""

from __future__ import annotations

import pytest

from mtgfish.data.db import CardDatabase
from mtgfish.paths import card_db_path


@pytest.fixture(scope="session")
def card_db() -> CardDatabase:
    """The local card snapshot.

    Tests that need real cards skip rather than fail when the database has not
    been built, so a fresh clone can still run the pure-rules suite. Build it
    with ``python -m mtgfish.tools.fetch_scryfall``.
    """
    path = card_db_path()
    if not path.exists():
        pytest.skip(f"card database not built ({path}); run mtgfish.tools.fetch_scryfall")
    db = CardDatabase(path)
    db.registry()  # Install the subtype registry process-wide.
    yield db
    db.close()
