"""Check the parser by hand against cards drawn completely at random.

    python -m mtgfish.tools.spot_check sample --count 500 --out docs/spot-check/sheet.md
    python -m mtgfish.tools.spot_check summarize docs/spot-check/sheet.md

``sample`` draws cards uniformly from the pool the parser is held to (every
card legal in at least one format) with the operating system's randomness -
no seed, so no one can pick a flattering sample, and no weighting by play
rate, so the long tail is checked as often as it occurs. Each card is written
with its oracle text beside the parser's reading of every ability and a
``VERDICT:`` line for the reviewer to fill in.

Verdicts, one per card:

``OK``       everything read, and the reading is what the card does
``WRONG``    something read as understood does something else - the worst
             outcome, a card that plays wrong while counted as understood
``PARTIAL``  some abilities unread (honestly), the ones read are right
``UNREAD``   nothing usable read, and the card says so
``UNSURE``   the reading cannot be judged from the rendering alone

Anything after the verdict word is the reviewer's note. ``summarize`` counts
them and lists the WRONG cards with their notes.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

VERDICTS = ("OK", "WRONG", "PARTIAL", "UNREAD", "UNSURE")


def draw(db, count: int) -> list:
    pool = list(db.iter_cards(format_legal_only=True))
    return random.SystemRandom().sample(pool, min(count, len(pool)))


def _card_block(number: int, card) -> str:
    from ..parser import parse_card
    from ..parser.explain import explain_card

    parsed = parse_card(card)
    status = "fully read" if parsed.fully_parsed else "NOT fully read"
    lines = [f"### {number}. {card.name}  ({status})", ""]
    for index, face in enumerate(card.faces):
        head = f"**{face.name}** - {face.type_line}" if len(card.faces) > 1 else str(face.type_line)
        lines.append(f"{head}  ")
        lines += [f"> {line}" for line in (face.oracle_text or "(no text)").splitlines()]
        lines.append("")
    for row in explain_card(parsed):
        mark = "INERT" if row["inert"] else "read"
        face = f"[face {row['face']}] " if len(card.faces) > 1 else ""
        lines.append(f"- {face}`{row['oracle']}`  ")
        lines.append(f"  -> ({mark}) {row['understood']}")
    for face in parsed.faces:
        for failure in face.failures:
            lines.append(f"- FAILED: `{failure.text}` - {failure.reason}")
    lines += ["", "VERDICT: ", ""]
    return "\n".join(lines)


def cmd_sample(args) -> int:
    from ..data.db import CardDatabase

    db = CardDatabase()
    db.registry()
    cards = draw(db, args.count)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"# Spot check: {len(cards)} cards drawn at random",
        "",
        (
            f"Drawn {time.strftime('%Y-%m-%d %H:%M')} from"
            f" {sum(1 for _ in db.iter_cards(format_legal_only=True))} cards legal in at"
            " least one format, uniformly, with the OS random source."
        ),
        f"Snapshot {db.content_hash[:16]}. Verdicts: {', '.join(VERDICTS)} (see mtgfish/tools/spot_check.py).",
        "",
    ]
    blocks = [_card_block(i, card) for i, card in enumerate(cards, 1)]
    out.write_text("\n".join(header) + "\n" + "\n".join(blocks))
    out.with_suffix(".json").write_text(
        json.dumps([{"name": c.name, "oracle_id": c.oracle_id} for c in cards], indent=1)
    )
    print(f"wrote {len(cards)} cards to {out}")
    return 0


_HEAD = re.compile(r"^### (\d+)\. (.+?)  \((fully read|NOT fully read)\)$")
_VERDICT = re.compile(r"^VERDICT:\s*(\S+)?\s*(.*)$")


def read_verdicts(path: Path) -> list[dict]:
    rows, current = [], None
    for line in path.read_text().splitlines():
        head = _HEAD.match(line)
        if head:
            current = {"name": head.group(2), "read": head.group(3) == "fully read"}
            continue
        verdict = _VERDICT.match(line)
        if verdict and current is not None:
            word = (verdict.group(1) or "").upper().rstrip(":,.")
            current["verdict"] = word if word in VERDICTS else ""
            current["note"] = verdict.group(2).strip()
            rows.append(current)
            current = None
    return rows


def cmd_summarize(args) -> int:
    rows = read_verdicts(Path(args.sheet))
    counts = Counter(row["verdict"] or "(blank)" for row in rows)
    print(f"{len(rows)} cards")
    for verdict in (*VERDICTS, "(blank)"):
        if counts.get(verdict):
            print(f"  {verdict:8} {counts[verdict]:4}  ({100 * counts[verdict] / len(rows):.1f}%)")
    read = [r for r in rows if r["read"]]
    if read:
        wrong = sum(1 for r in read if r["verdict"] == "WRONG")
        print(f"of {len(read)} counted fully read, {wrong} wrong ({100 * wrong / len(read):.1f}%)")
    for row in rows:
        if row["verdict"] in ("WRONG", "UNSURE"):
            print(f"  {row['verdict']}: {row['name']} - {row['note']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--count", type=int, default=500)
    sample.add_argument("--out", default="docs/spot-check/sheet.md")
    sample.set_defaults(func=cmd_sample)
    summarize = sub.add_parser("summarize")
    summarize.add_argument("sheet")
    summarize.set_defaults(func=cmd_summarize)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
