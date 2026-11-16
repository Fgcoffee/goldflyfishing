"""Spot-check a parser batch: sample cards that became fully read, and show
what the parser now understands them to do.

    python -m tests.ir.readings_spot_check BASE_REV [--sample 30] [--seed 0]

Parses the whole pool at ``BASE_REV`` (in a temporary git worktree) and at the
working tree, then prints a random sample of the cards that are fully read now
but were not then: each ability's Oracle line beside its ``explain_card``
reading, for checking by hand. Misreads go to
``engine/status/readings_queue.md``; good readings can join the golden set
(``python -m tests.ir.test_golden_readings NAME``).

Not a test (no ``test_`` prefix): it parses ~32,000 cards twice, which takes
a few minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Run in the tree being measured: prints the oracle ids of fully-read cards.
_FULLY_READ = r"""
import json, os, sys
os.environ["MTGFISH_PARSE_STORE"] = "off"
from concurrent.futures import ProcessPoolExecutor
from mtgfish.data.db import CardDatabase

def chunk(names):
    from mtgfish.data.db import CardDatabase
    from mtgfish.parser.compile import parse_card
    db = CardDatabase(sys.argv[1]); db.registry()
    out = []
    for name in names:
        card = db.lookup(name)
        if card is not None and parse_card(card).fully_parsed:
            out.append(name)
    return out

if __name__ == "__main__":
    db = CardDatabase(sys.argv[1]); db.registry()
    names = [card.name for card in db.iter_by_play_rate()]
    db.close()
    size = 500
    with ProcessPoolExecutor() as pool:
        parts = pool.map(chunk, [names[i:i + size] for i in range(0, len(names), size)])
        print(json.dumps(sorted(n for part in parts for n in part)))
"""


def fully_read(tree: Path, db_path: str) -> set[str]:
    reply = subprocess.run(
        [sys.executable, "-c", _FULLY_READ, db_path],
        cwd=tree, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(tree)},
    )
    return set(json.loads(reply.stdout.strip().splitlines()[-1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", help="the revision before the batch, e.g. HEAD~3")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    os.environ["MTGFISH_PARSE_STORE"] = "off"
    from mtgfish.paths import card_db_path

    # Both sides read the same card database, so only the parser differs.
    db_path = str(card_db_path())
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch) / "base"
        subprocess.run(["git", "worktree", "add", "--detach", str(base), args.base],
                       cwd=ROOT, check=True, capture_output=True)
        try:
            before = fully_read(base, db_path)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(base)],
                           cwd=ROOT, capture_output=True)
    after = fully_read(ROOT, db_path)

    gained = sorted(after - before)
    lost = sorted(before - after)
    print(f"fully read: {len(before)} at {args.base}, {len(after)} now "
          f"(+{len(gained)}, -{len(lost)})")
    if lost:
        print("no longer fully read:", ", ".join(lost[:50]))

    from mtgfish.data.db import CardDatabase
    from mtgfish.parser.compile import parse_card
    from mtgfish.parser.explain import explain_card

    db = CardDatabase(db_path)
    db.registry()
    for name in random.Random(args.seed).sample(gained, min(args.sample, len(gained))):
        card = db.lookup(name)
        print(f"\n## {name} - {' // '.join(str(face.type_line) for face in card.faces)}")
        for row in explain_card(parse_card(card)):
            print(f"  O: {row['oracle']}")
            print(f"  U: {row['understood']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
