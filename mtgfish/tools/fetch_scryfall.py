"""Build (or rebuild) the local card database.

    python -m mtgfish.tools.fetch_scryfall

Downloads Scryfall's oracle-card, rulings, and tagger dumps, then writes a
pruned SQLite snapshot. Safe to re-run: bulk files are cached on Scryfall's own
``updated_at``, so nothing is re-downloaded until the pool actually changes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..data.db import CardDatabase, build_database
from ..data.scryfall import ScryfallClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-rulings", action="store_true", help="skip the rulings dump")
    parser.add_argument("--no-tags", action="store_true", help="skip the tagger dump")
    parser.add_argument("--output", type=str, default=None, help="database path")
    args = parser.parse_args(argv)

    started = time.monotonic()
    path = build_database(
        path=Path(args.output) if args.output else None,
        client=ScryfallClient(),
        include_rulings=not args.no_rulings,
        include_tags=not args.no_tags,
        progress=lambda msg: print(f"  {msg}", flush=True),
    )
    elapsed = time.monotonic() - started

    with CardDatabase(path) as db:
        legal = sum(1 for _ in db.iter_cards(commander_legal_only=True))
        print(f"\n{db.card_count} cards ({legal} Commander-legal) in {elapsed:.1f}s")
        print(f"snapshot hash: {db.content_hash[:16]}")
        print(f"database: {path} ({path.stat().st_size / 1e6:.1f} MB)")

        unknown = json.loads(db.meta("unknown_type_words", "{}") or "{}")

    if unknown:
        # A Commander-legal card whose type line we cannot fully read means the
        # engine is missing a card type. Loud and non-zero, never a warning
        # buried in a log.
        print("\nUNKNOWN TYPE WORDS ON COMMANDER-LEGAL CARDS:", file=sys.stderr)
        for word, examples in unknown.items():
            print(f"  {word!r}: {', '.join(examples)}", file=sys.stderr)
        print(
            "\nAdd these to CardType/Supertype in mtgfish/rules/enums.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
