"""Parse coverage over the cards people actually play.

Coverage across the whole pool answers the wrong question. The pool is 31,830
commander-legal cards and most of them appear in no deck anybody has ever
built, so a single percentage weights a format staple and a draft common
equally. It cannot tell you whether a real decklist will simulate correctly,
which is the only thing the number is for.

EDHREC's rank rides along on every Scryfall card, so ordering by play rate
costs nothing and needs no second data source.

Two outputs, and the second is the one to work from:

* coverage at several depths, so "how much of what people play do we read?"
  has an answer;
* the failure queue **restricted to the top N**, grouped by the construct that
  stopped the parse - which is a list of grammar rules to write, ordered by how
  much real play they unblock.
"""

from __future__ import annotations

import argparse
import collections

from ..data.db import CardDatabase
from ..parser import parse_card

#: Depths worth reporting. The first few are the cards in every deck; the last
#: is roughly "everything anyone plays on purpose".
DEPTHS = (100, 250, 500, 1000, 2500, 5000, 10000)


def coverage(db: CardDatabase, depths=DEPTHS) -> list[tuple[int, int, int]]:
    """(depth, cards read, cards checked) at each depth."""
    ranked = list(db.iter_by_play_rate(limit=max(depths)))
    verdicts = [parse_card(card).fully_parsed for card in ranked]

    out = []
    for depth in depths:
        sample = verdicts[:depth]
        if not sample:
            continue
        out.append((depth, sum(sample), len(sample)))
    return out


def queue(db: CardDatabase, depth: int, limit: int = 30):
    """What stops the parse, among the top ``depth`` played cards.

    Grouped by the first few tokens at the give-up point, because a hundred
    cards blocked on the same phrase are one rule to write, not a hundred
    problems.
    """
    clusters: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)

    for card in db.iter_by_play_rate(limit=depth):
        for failure in parse_card(card).failures:
            words = [w.lower() for w in failure.remaining[:4]]
            if words:
                key = " ".join(
                    "{M}" if w.startswith("{") else ("N" if w.isdigit() else w)
                    for w in words
                )
            else:
                key = f"(the ability's shape, not its text: {failure.rule})"
            clusters[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(f"{card.name}: {failure.text[:70]}")

    # The true totals, not the totals of what is displayed. Summing only the
    # shown clusters made the headline grow when --limit grew, which is a
    # report that reads as a regression whenever you ask it for more detail.
    return (
        clusters.most_common(limit),
        examples,
        sum(clusters.values()),
        len(clusters),
    )


def render(db: CardDatabase, depth: int, limit: int) -> str:
    lines = ["Parse coverage by how often a card is actually played", ""]
    lines.append(f"{'top N played':>14}  {'read':>6}  {'of':>6}   share")
    for at, read, total in coverage(db):
        bar = "#" * int(round(20 * read / total))
        lines.append(f"{at:>14}  {read:6}  {total:6}   {read / total:6.1%}  {bar}")

    ranked, examples, total, distinct = queue(db, depth, limit)
    shown = sum(count for _, count in ranked)
    lines += [
        "",
        f"What stops the parse among the top {depth} played cards: "
        f"{total} failing abilities in {distinct} clusters, "
        f"top {len(ranked)} shown ({shown / total:.0%} of them)."
        if total
        else f"Nothing fails among the top {depth} played cards.",
        "",
    ]
    for phrase, count in ranked:
        lines.append(f"  {count:4}  {phrase}")
        for example in examples[phrase][:2]:
            lines.append(f"        {example}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--depth",
        type=int,
        default=1000,
        help="how many of the most-played cards the work queue covers",
    )
    parser.add_argument("--limit", type=int, default=30, help="queue entries to show")
    args = parser.parse_args(argv)

    db = CardDatabase()
    db.registry()
    print(render(db, args.depth, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
