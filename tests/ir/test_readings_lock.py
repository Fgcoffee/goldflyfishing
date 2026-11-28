"""The card database changes only on purpose (schema/readings.lock).

Every format-pool card's reading is compared with the committed lock. A
parser change that moves a reading fails here until the lock is rewritten in
the same commit:

    python -m mtgfish.tools.readings_lock write

and the lock's diff is then the list of cards whose reading moved.
"""

from __future__ import annotations

import pytest

from mtgfish.tools import readings_lock


def test_every_reading_matches_the_lock(card_db):
    if not readings_lock.LOCK.exists():
        pytest.fail("schema/readings.lock is missing: python -m mtgfish.tools.readings_lock write")
    moved = readings_lock.differences(readings_lock.load(), readings_lock.current())
    assert not moved, (
        f"{len(moved)} card readings differ from schema/readings.lock. If the change is "
        "intended, run `python -m mtgfish.tools.readings_lock write` and commit the lock:\n"
        + "\n".join(moved[:40])
    )


def test_differences_name_each_kind_of_move():
    locked = {"a": ("read", "1", "Alpha"), "b": ("partial", "2", "Beta"), "c": ("read", "3", "Gamma")}
    now = [("a", "read", "9", "Alpha"), ("b", "read", "2", "Beta"), ("d", "partial", "4", "Delta")]
    assert readings_lock.differences(locked, now) == [
        "~ Alpha: read, reading changed",
        "~ Beta: partial -> read",
        "- Gamma: no longer in the pool",
        "+ Delta: new card, partial",
    ]
