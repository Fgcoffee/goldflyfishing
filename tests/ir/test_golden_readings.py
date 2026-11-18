"""Golden readings: the most-played cards, read correctly, pinned.

Coverage numbers say whether a card was read *completely*. They cannot say it
was read *right*. These cards were checked by hand: each ability's Oracle text
against what the parser understood (``explain_card``, which renders from the
opcodes, never from the text). The ones that matched are stored in
``golden/readings.json``; any later change to how one of them reads fails here,
by name, with the old and new sentence side by side.

A change here is not necessarily a regression - the parser may have learned to
say something more precisely. Check the new reading against the Oracle text,
then refresh just that card:

    python -m tests.ir.test_golden_readings "Sol Ring" "Command Tower"

Cards that read *wrong* are not stored here; they are queued for the parser in
``engine/status/readings_queue.md``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "readings.json"


def _load() -> dict[str, list[dict]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def reading(card) -> list[dict]:
    """What the parser understood a card to do, one row per ability."""
    from mtgfish.parser.compile import parse_card
    from mtgfish.parser.explain import explain_card

    return [
        {"oracle": row["oracle"], "understood": row["understood"]}
        for row in explain_card(parse_card(card))
    ]


NAMES = sorted(_load())


@pytest.mark.parametrize("name", NAMES)
def test_golden_reading(card_db, name):
    card = card_db.lookup(name)
    assert card is not None, f"{name} is no longer in the card database"
    expected = _load()[name]
    actual = reading(card)
    if actual != expected:
        lines = [f"{name} reads differently from its checked reading:"]
        for index in range(max(len(actual), len(expected))):
            old = expected[index] if index < len(expected) else None
            new = actual[index] if index < len(actual) else None
            if old == new:
                continue
            if old and new and old["oracle"] != new["oracle"]:
                # Scryfall rewords cards; the reading may still be right.
                lines.append(f"  Oracle was: {old['oracle']}")
                lines.append(f"  Oracle now: {new['oracle']}")
            else:
                lines.append(f"  Oracle: {(old or new)['oracle']}")
            lines.append(f"    was: {old['understood'] if old else '(no such ability)'}")
            lines.append(f"    now: {new['understood'] if new else '(ability gone)'}")
        lines.append("If the new reading is right, refresh it: python -m tests.ir.test_golden_readings "
                     f'"{name}"')
        pytest.fail("\n".join(lines))


def test_golden_cards_are_fully_read(card_db):
    from mtgfish.parser.compile import parse_card

    partial = [name for name in NAMES if not parse_card(card_db.lookup(name)).fully_parsed]
    assert not partial, f"golden cards no longer fully read: {partial}"


def _refresh(names: list[str]) -> None:
    """Re-store the named cards' current readings, after checking them by hand."""
    import os

    os.environ["MTGFISH_PARSE_STORE"] = "off"
    from mtgfish.data.db import CardDatabase

    db = CardDatabase()
    db.registry()
    golden = _load()
    for name in names:
        card = db.lookup(name)
        if card is None:
            raise SystemExit(f"no card named {name!r}")
        golden[card.name] = reading(card)
        print(f"stored {card.name}")
    GOLDEN.write_text(json.dumps(golden, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                      encoding="utf-8")


if __name__ == "__main__":
    _refresh(sys.argv[1:])
