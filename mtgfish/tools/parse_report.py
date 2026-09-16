"""Parser coverage over the real card pool.

    python -m mtgfish.tools.parse_report [--limit N] [--card NAME]

The stopping-point table is the work queue: each row is a token the grammar
cannot get past, and the count is how many abilities it costs. Writing the rule
that handles the top row is always the highest-value next change.
"""

from __future__ import annotations

import argparse

from ..data.db import CardDatabase
from ..parser import measure, parse_card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="stop after N cards")
    parser.add_argument("--card", help="show one card's parse in detail")
    parser.add_argument(
        "--failures", type=int, default=0, help="print N example failures"
    )
    parser.add_argument(
        "--crosscheck",
        action="store_true",
        help="compare fully-parsed cards against Scryfall's oracle tags, which "
        "is the only way to catch a card that parsed but parsed wrongly",
    )
    args = parser.parse_args(argv)

    db = CardDatabase()
    db.registry()

    if args.card:
        return _one_card(db, args.card)

    cards = db.iter_cards(commander_legal_only=True)
    if args.limit:
        cards = _take(cards, args.limit)

    if args.crosscheck:
        from ..parser.crosscheck import cross_check

        print(cross_check(cards, db).render())
        return 0

    report = measure(cards)
    print(report.render())

    if args.failures:
        print("\nExamples:")
        for failure in report.failures[: args.failures]:
            print(f"  {failure}")
    return 0


def _one_card(db: CardDatabase, name: str) -> int:
    card = db.lookup(name)
    if card is None:
        print(f"no such card: {name}")
        return 1

    parsed = parse_card(card)
    print(f"{card.name}\n")
    for index, face in enumerate(parsed.faces):
        if len(parsed.faces) > 1:
            print(f"-- face {index} --")
        for ability in face.abilities:
            hollow = any(
                node.is_unparsed
                for effect in ability.effects
                for node in effect.walk()
            )
            # Three states, not two: read, unreadable, and "shaped correctly
            # but does nothing" - which looks like success everywhere else.
            mark = "!" if ability.unparsed else ("~" if hollow else " ")
            print(f" {mark} {ability}")
        for failure in face.failures:
            print(f"   FAILED: {failure}")
    print()
    print("fully parsed" if parsed.fully_parsed else "NOT fully parsed")
    return 0


def _take(iterable, count: int):
    for index, item in enumerate(iterable):
        if index >= count:
            return
        yield item


if __name__ == "__main__":
    raise SystemExit(main())
