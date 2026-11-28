"""The readings lock: every card's reading, committed, so the card database
only changes on purpose.

    python -m mtgfish.tools.readings_lock check    # what moved since the lock
    python -m mtgfish.tools.readings_lock write    # accept the current readings

``schema/readings.lock`` holds one line per card in the format pool, sorted
by oracle id:

    <oracle_id> TAB <state> TAB <hash> TAB <name>

``state`` is ``read`` (every ability fully read), ``partial`` (something on
the card isn't) or ``notext``. ``hash`` is a digest of the abilities in the
card's IR document, the JSON the engine plays (``mtgfish.ir.store
.card_document``), so a reading that changes in any way the engine could see
changes its line. Plain text, so a git diff of the lock names every card
whose reading moved.

``tests/ir/test_readings_lock.py`` fails while the lock and the parser
disagree. A parser change therefore updates the lock in the same commit
(``write``), and the diff is the list of cards to look at. The parse store
(``mtgfish/parser/store.py``) is still only a cache; the lock is a record of
what was accepted, checked against the parser, never read in its place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "schema" / "readings.lock"

HEADER = (
    "# The readings lock (mtgfish/tools/readings_lock.py): oracle_id, state, IR hash, name.\n"
    "# Regenerate with `python -m mtgfish.tools.readings_lock write` in the commit that changes a reading.\n"
)

_DB = None


def _worker_db():
    global _DB
    if _DB is None:
        from ..data.db import CardDatabase

        _DB = CardDatabase()
        _DB.registry()
    return _DB


def reading(card) -> tuple[str, str]:
    """``(state, hash)`` for one card, from the IR document the export writes."""
    from ..ir.store import card_document

    document = card_document(card)
    faces = document["faces"]
    abilities = [face["abilities"] for face in faces]
    if not any(abilities) and not any(face["failures"] for face in faces):
        state = "notext"
    elif all(face["fully_read"] for face in faces):
        state = "read"
    else:
        state = "partial"
    digest = hashlib.sha256(
        json.dumps(abilities, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return state, digest


def _chunk(oracle_ids: list[str]) -> list[tuple[str, str, str, str]]:
    db = _worker_db()
    out = []
    for oracle_id in oracle_ids:
        card = db.by_oracle_id(oracle_id)
        if card is None:
            continue
        try:
            state, digest = reading(card)
        except Exception as exc:  # noqa: BLE001 - one crash shouldn't hide the rest
            state, digest = "crashed", type(exc).__name__
        out.append((oracle_id, state, digest, card.name))
    return out


def current(jobs: int | None = None) -> list[tuple[str, str, str, str]]:
    """Every format-pool card's reading now, sorted by oracle id."""
    from .census import _pool_ids

    ids = [oracle_id for oracle_id, _ in _pool_ids("format")]
    jobs = jobs or os.cpu_count() or 1
    size = max(50, len(ids) // (jobs * 8) or 1)
    chunks = [ids[i : i + size] for i in range(0, len(ids), size)]
    rows: list[tuple[str, str, str, str]] = []
    if jobs == 1:
        for chunk in chunks:
            rows.extend(_chunk(chunk))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for result in pool.map(_chunk, chunks):
                rows.extend(result)
    rows.sort()
    return rows


def render(rows) -> str:
    return HEADER + "".join("\t".join(row) + "\n" for row in rows)


def load(path: Path = LOCK) -> dict[str, tuple[str, str, str]]:
    """``{oracle_id: (state, hash, name)}`` from a lock file."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        oracle_id, state, digest, name = line.split("\t", 3)
        out[oracle_id] = (state, digest, name)
    return out


def differences(locked: dict, rows) -> list[str]:
    """One line per card whose reading differs from the lock."""
    now = {oracle_id: (state, digest, name) for oracle_id, state, digest, name in rows}
    out = []
    for oracle_id in sorted(set(locked) | set(now)):
        before, after = locked.get(oracle_id), now.get(oracle_id)
        if before == after:
            continue
        name = (after or before)[2]
        if before is None:
            out.append(f"+ {name}: new card, {after[0]}")
        elif after is None:
            out.append(f"- {name}: no longer in the pool")
        elif before[0] != after[0]:
            out.append(f"~ {name}: {before[0]} -> {after[0]}")
        else:
            out.append(f"~ {name}: {after[0]}, reading changed")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("check", "write"))
    parser.add_argument("--jobs", type=int, default=None)
    args = parser.parse_args(argv)
    rows = current(args.jobs)
    if args.command == "write":
        LOCK.write_text(render(rows), encoding="utf-8", newline="\n")
        print(f"wrote {len(rows)} cards to {LOCK}")
        return 0
    moved = differences(load(), rows)
    for line in moved:
        print(line)
    print(f"{len(moved)} cards differ from {LOCK.name}")
    return 1 if moved else 0


if __name__ == "__main__":
    raise SystemExit(main())
