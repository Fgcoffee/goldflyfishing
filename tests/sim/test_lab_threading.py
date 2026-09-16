"""The swap lab runs on a worker thread, and sqlite does not travel.

A sqlite connection belongs to the thread that opened it. The bridge builds one
card database on the UI thread and keeps it for the life of the window, and the
lab worker read ``db.content_hash`` *from the worker thread* - so the feature
failed with a ProgrammingError before it had played a single game.

The ordinary Simulate button got away with it by accident: it happens to read
the same property on the UI thread and pass the resulting string across.

Two tests: the property is safe to read anywhere, and a whole comparison
survives being run from a thread that did not build the database.
"""

from __future__ import annotations

import threading

import pytest

from mtgfish.sim.lab import Slot, plan, run_lab

DECK = "\n".join(
    ["1 Sol Ring", "60 Forest", "38 Grizzly Bears", "", "// Commander", "1 Omnath, Locus of Mana"]
)


def test_the_content_hash_can_be_read_from_another_thread(card_db):
    """It is a property of the file, so it is read once and remembered.

    This is the exact call that broke the lab, isolated: everything else the
    UI hands across a thread boundary is already a plain value.
    """
    card_db.registry()
    seen: list = []

    def worker():
        try:
            seen.append(card_db.content_hash)
        except Exception as exc:  # noqa: BLE001 - the failure under test
            seen.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert isinstance(seen[0], str), seen[0]
    assert seen[0] == card_db.content_hash


def test_a_comparison_runs_from_a_thread_that_did_not_open_the_database(card_db):
    """The whole path, the way the bridge drives it.

    Small enough that the runner stays in-process - which is the case that
    failed, because the parallel path builds a database inside each worker
    process and never sees the UI thread's one at all.
    """
    card_db.registry()
    variants, problems = plan(DECK, [Slot("Sol Ring", ("Mana Crypt",))], card_db)
    assert not problems
    assert len(variants) == 2

    # Read here, on the thread that owns the connection, exactly as the bridge
    # now does - the point being that the worker is handed a string.
    card_db_hash = card_db.content_hash
    outcome: list = []

    def worker():
        try:
            outcome.append(
                run_lab(
                    variants,
                    opponents=(DECK,) * 3,
                    games=2,
                    run_seed=1,
                    card_db_hash=card_db_hash,
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=300)

    result = outcome[0]
    if isinstance(result, Exception):
        pytest.fail(f"the lab failed on a worker thread: {result!r}")
    assert len(result.variants) == 2
    assert all(variant.report is not None for variant in result.variants)
