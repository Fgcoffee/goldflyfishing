"""Shared test fixtures."""

from __future__ import annotations

import pytest

from mtgfish.data.db import CardDatabase
from mtgfish.paths import card_db_path
from mtgfish.rules.enums import CardType
from mtgfish.rules.typeline import SubtypeRegistry, active_registry, install_registry


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


#: Creature types named by tests that exercise the grammar on hand-written text.
#: A short fixed list, not a sample of the real catalog: the point is that the
#: parser can read a type line naming a creature type, not which types exist.
TEST_CREATURE_TYPES = ("Soldier", "Goblin", "Elf", "Beast", "Zombie", "Time Lord")


@pytest.fixture
def subtype_registry() -> SubtypeRegistry:
    """A subtype registry for tests that name a creature type but need no cards.

    The parser asks ``active_registry()`` whether a word is a subtype, and until
    the card database is loaded that registry is a stub knowing almost nothing -
    so "Create a 1/1 white Soldier creature token." does not parse. Requesting
    ``card_db`` would make such a test skip on a fresh clone; requesting nothing
    made it pass only when some earlier test in the same process happened to
    load the database first. This installs a known registry for the duration of
    one test and puts the previous one back, so the result is the same either
    way.
    """
    previous = active_registry()
    registry = SubtypeRegistry()
    registry.register(CardType.CREATURE, TEST_CREATURE_TYPES)
    install_registry(registry)
    try:
        yield registry
    finally:
        install_registry(previous)
