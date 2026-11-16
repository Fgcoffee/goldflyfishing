"""The dummy-ability registry, with how much play each shape would unlock.

    python -m mtgfish.tools.dummy_report CENSUS.jsonl.gz

A dummy is a shape the parser recognises and the engine can't do yet
(``mtgfish.parser.dummies``). This lists every registered shape with the
cards that use it: all of them, and those in the top 1,000 and 5,000 by play
rate. Read from a census file (``python -m mtgfish.tools.census --out``), so
it costs nothing beyond the census already run. It is the rules session's
backlog, ordered by play.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import defaultdict

from ..parser.dummies import DUMMIES

_MARK = re.compile(r"\(recognised, not implemented yet: ([a-z0-9-]+)\)")


def tally(path: str) -> dict[str, dict]:
    """Per dummy name: cards using it, overall and by play-rate depth."""
    out: dict[str, dict] = {d.name: {"cards": set(), "top1000": set(), "top5000": set()} for d in DUMMIES}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rank = row.get("rank")
            for ability in row["abilities"]:
                for name in _MARK.findall(ability.get("ir", "")):
                    entry = out.setdefault(name, {"cards": set(), "top1000": set(), "top5000": set()})
                    entry["cards"].add(row["name"])
                    if rank is not None and rank <= 1000:
                        entry["top1000"].add(row["name"])
                    if rank is not None and rank <= 5000:
                        entry["top5000"].add(row["name"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("census", help="a census .jsonl.gz")
    args = parser.parse_args(argv)
    counts = tally(args.census)
    needs = {d.name: d for d in DUMMIES}
    print(f"{'dummy':34} {'top1000':>8} {'top5000':>8} {'cards':>6}  what the engine would need")
    order = sorted(counts, key=lambda n: (-len(counts[n]["top1000"]), -len(counts[n]["top5000"]), -len(counts[n]["cards"])))
    for name in order:
        entry = counts[name]
        dummy = needs.get(name)
        what = (dummy.needs + (f" (CR {dummy.rule})" if dummy.rule else "")) if dummy else "(not in the registry)"
        print(f"{name:34} {len(entry['top1000']):>8} {len(entry['top5000']):>8} {len(entry['cards']):>6}  {what}")
        examples = sorted(entry["top1000"] or entry["cards"])[:4]
        if examples:
            print(f"{'':34}   e.g. {', '.join(examples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
