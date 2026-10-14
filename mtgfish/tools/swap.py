"""Compare card swaps from the command line.

The same thing the Swap lab tab does, without a window - useful for a long
comparison you want to leave running, and for checking the feature works in
the packaged build where there is no console to read a traceback from.

    python -m mtgfish.tools.swap deck.txt \\
        --swap "Sol Ring=Mana Crypt,Mana Vault" \\
        --swap "Llanowar Elves=Birds of Paradise" \\
        --games 2000

Each ``--swap`` is one slot: the card in the deck, then the candidates to try
in its place. By default the slots apply *together* - one deck carrying every
change. ``--separate`` tries each candidate on its own instead, which is the
only way to learn which single change did the work.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from ..data.db import CardDatabase
from ..sim.lab import Slot, comparison, plan, run_lab


def _read(reference: str) -> str:
    """A decklist from a file, or the text itself if it is not one."""
    path = pathlib.Path(reference)
    if path.exists():
        return path.read_text(encoding="utf8")
    return reference


def _slot(spec: str) -> Slot:
    """"Sol Ring=Mana Crypt,Mana Vault" into a slot."""
    original, _, rest = spec.partition("=")
    if not original.strip() or not rest.strip():
        raise argparse.ArgumentTypeError(
            f"{spec!r} should look like 'Card In Deck=First Candidate,Second Candidate'"
        )
    return Slot(
        original=original.strip(),
        candidates=tuple(name.strip() for name in rest.split(",") if name.strip()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deck", help="decklist file, or the decklist itself")
    parser.add_argument(
        "--swap",
        action="append",
        default=[],
        type=_slot,
        metavar="CARD=A,B,C",
        help="a card to replace and what to try in its place; repeat for more",
    )
    parser.add_argument(
        "--separate",
        action="store_true",
        help="try each swap on its own instead of applying them all together",
    )
    parser.add_argument("--opponents", nargs="*", default=[])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    db = CardDatabase()
    db.registry()

    decklist = _read(args.deck)
    opponents = tuple(_read(text) for text in args.opponents) or (decklist,) * 3
    while len(opponents) < 3:
        opponents = (*opponents, decklist)

    variants, problems = plan(
        decklist, args.swap, db, combine=not args.separate
    )
    for problem in problems:
        print(f"! {problem}", file=sys.stderr)
    if len(variants) < 2:
        print("nothing to compare", file=sys.stderr)
        return 2

    print(f"{len(variants)} decks x {args.games} games on seed {args.seed}")
    result = run_lab(
        variants,
        opponents=opponents[:3],
        games=args.games,
        run_seed=args.seed,
        card_db_hash=db.content_hash,
        progress=lambda done, total: print(
            f"\r{done}/{total}", end="", file=sys.stderr, flush=True
        ),
    )
    print(file=sys.stderr)

    rows = comparison(result)
    width = max(len(row["label"]) for row in rows)
    print()
    print(f"{'deck'.ljust(width)}  {'win':>7}  {'delta':>7}  {'win turn':>8}  {'delta':>7}")
    for row in rows:
        if row.get("error"):
            print(f"{row['label'].ljust(width)}  {row['error']}")
            continue
        delta_rate = row.get("delta_win_rate")
        delta_turn = row.get("delta_win_round")
        print(
            f"{row['label'].ljust(width)}  "
            f"{row['win_rate'] * 100:6.2f}%  "
            f"{'' if delta_rate is None else f'{delta_rate * 100:+6.2f}%':>7}  "
            f"{row['average_win_round']:8.2f}  "
            f"{'' if delta_turn is None else f'{delta_turn:+7.2f}':>7}"
        )

    for problem in result.problems:
        print(f"! {problem}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
