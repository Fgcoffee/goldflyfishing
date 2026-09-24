"""Shared test fixtures."""

from __future__ import annotations

import pytest

from mtgfish.data.db import CardDatabase
from mtgfish.paths import card_db_path
from mtgfish.rules.cr200_parts_of_a_card.cr205_typeline import (
    SubtypeRegistry,
    active_registry,
    install_registry,
)
from mtgfish.rules.kernel.enums import CardType


@pytest.fixture(scope="session")
def card_db() -> CardDatabase:
    """The local card snapshot.

    Built from the dumps in the repository if it is not there yet, which takes
    a few seconds and needs no network - so a fresh clone runs the whole suite
    rather than skipping every test that needs a real card. Only a checkout
    with no dumps either skips.
    """
    from mtgfish.bootstrap import build_from_dumps, pool_is_stale

    path = card_db_path()
    if not path.exists() or pool_is_stale(path):
        try:
            built = build_from_dumps(report=lambda message: print(f"  {message}", flush=True))
        except OSError as exc:  # in use by a running server, on Windows
            if not path.exists():
                pytest.skip(f"cannot build the card database: {exc}")
            built = None
        if built is None and not path.exists():
            pytest.skip(f"no card database ({path}) and no Scryfall dumps to build one from")
        path = built or path
    db = CardDatabase(path)
    db.registry()  # Install the subtype registry process-wide.
    yield db
    db.close()


#: Creature types named by tests that exercise the grammar on hand-written text.
#: A short fixed list, not a sample of the real catalog: the point is that the
#: parser can read a type line naming a creature type, not which types exist.
TEST_CREATURE_TYPES = ("Soldier", "Goblin", "Elf", "Beast", "Zombie", "Time Lord")

#: Artifact types, for the same reason and one more: "create a Treasure token"
#: names no card *type* at all, so the parser has to ask the registry what a
#: Treasure is. Without these it can only answer "I don't know", which is the
#: right answer to give and the wrong one to write a test against.
TEST_ARTIFACT_TYPES = ("Treasure", "Clue", "Food", "Equipment")


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
    registry.register(CardType.ARTIFACT, TEST_ARTIFACT_TYPES)
    install_registry(registry)
    try:
        yield registry
    finally:
        install_registry(previous)
