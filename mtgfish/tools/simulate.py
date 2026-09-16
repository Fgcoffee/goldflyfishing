"""Run a goldfishing simulation from the command line.

    python -m mtgfish.tools.simulate deck.txt --games 10000
    python -m mtgfish.tools.simulate deck.txt --opponents b.txt c.txt d.txt
    python -m mtgfish.tools.simulate deck.txt --games 200 --replay 42

A deck is a decklist file or an Archidekt URL. With no opponents the deck is
played against three copies of itself, which is the honest default for a
goldfish: it measures the deck against a known quantity rather than against
whatever three decks happened to be lying around.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..data.db import CardDatabase
from ..sim import RunConfig, render, replay_game, run, summarize, verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deck", help="decklist file or Archidekt URL")
    parser.add_argument(
        "--opponents", nargs="*", default=[], help="up to three opposing decks"
    )
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=25, help="turn cap, per player")
    parser.add_argument("--workers", type=int, default=0, help="0 picks a sane default")
    parser.add_argument(
        "--replay", type=int, help="after the run, print this game turn by turn"
    )
    parser.add_argument(
        "--out",
        help="write the report here as well as to stdout (and the raw "
        "per-game records beside it as JSON, for the GUI to chart)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the determinism check (it replays a handful of games)",
    )
    args = parser.parse_args(argv)

    db = CardDatabase()
    db.registry()

    try:
        decklists = _load_decks(args.deck, args.opponents)
    except OSError as exc:
        print(f"could not read a deck: {exc}", file=sys.stderr)
        return 1

    config = RunConfig(
        decklists=decklists,
        games=args.games,
        max_rounds=args.rounds,
        run_seed=args.seed,
        workers=args.workers,
        card_db_hash=db.content_hash,
    )

    print(f"Simulating {args.games} games...", file=sys.stderr)
    started = time.time()
    result = run(config, progress=_progress)
    elapsed = time.time() - started
    print(file=sys.stderr)

    mismatches = [] if args.no_verify else verify(config, result)
    result.unparsed_cards = _inert_cards(db, decklists[0])
    report = summarize(result, mismatches=mismatches)

    text = render(report)
    print(text)
    if args.out:
        _write(Path(args.out), text, report, config)
    print()
    print(
        f"{elapsed:.1f}s for {args.games} games"
        f"  ({elapsed / max(1, args.games) * 1000:.0f}ms each)"
    )

    if args.replay is not None:
        _print_replay(config, args.replay)
    return 0


def _write(path: Path, text: str, report, config: RunConfig) -> None:
    """Save the report, and the numbers behind it.

    The JSON is what a chart is built from, and it is deliberately not the
    games: every series point carries the indices of a few games that made it,
    and those replay from the seed. A run's charts therefore cost kilobytes
    rather than gigabytes.
    """
    import json

    path.write_text(text + "\n", encoding="utf8")
    data = {
        "config": {
            "games": config.games,
            "run_seed": config.run_seed,
            "max_rounds": config.max_rounds,
            "hero": config.hero,
            "card_db_hash": config.card_db_hash,
        },
        "win_rate": report.win_rate,
        "stall_rate": report.stall_rate,
        "win_reasons": dict(report.win_reasons),
        "commander_landed": dict(report.commander_landed),
        "commander_never": report.commander_never,
        "cards": [
            {
                "name": card.name,
                "impact": card.impact,
                "margin": card.margin,
                "drawn": card.drawn,
                "cast": card.cast,
                "average_cast_turn": card.average_cast_turn,
            }
            for card in report.cards
        ],
        "removal": [
            {
                "victim": target.victim,
                "losses": target.losses,
                "by_source": dict(target.by_source),
                "by_method": dict(target.by_method),
            }
            for target in report.removal
        ],
        "series": [
            {
                "metric": series.metric,
                "player": series.player,
                "turns": series.turns,
                "values": series.values,
                "samples": series.samples,
                "examples": series.examples,
            }
            for series in report.series
        ],
    }
    path.with_suffix(".json").write_text(json.dumps(data), encoding="utf8")


def _progress(done: int, total: int) -> None:
    print(f"\r  {done}/{total}", end="", file=sys.stderr, flush=True)


def _load_decks(deck: str, opponents: list[str]) -> tuple[str, ...]:
    """The hero's deck, then three opponents.

    A missing opponent is filled with a copy of the hero's deck rather than
    left out. A four-player game against two opponents is a different format
    with different maths, and quietly running one would make the win rate
    meaningless.
    """
    lists = [_read(deck)]
    lists.extend(_read(path) for path in opponents[:3])
    while len(lists) < 4:
        lists.append(lists[0])
    return tuple(lists)


def _read(source: str) -> str:
    """A decklist, from a file or from Archidekt.

    Archidekt is fetched here and turned into text immediately, so a run's
    configuration is a tuple of decklists rather than a URL - which keeps a
    report reproducible after the deck has been edited online.
    """
    from ..data.decks.archidekt import is_archidekt_url

    if is_archidekt_url(source):
        from ..data.decks.archidekt import fetch_archidekt

        return fetch_archidekt(source)
    return Path(source).read_text(encoding="utf8")


def _inert_cards(db: CardDatabase, decklist: str) -> list[str]:
    """Cards in the deck the parser could not read.

    Surfaced with the report because every number in it is standing on these:
    a deck with ten inert cards is being measured as a deck with ten blanks.
    """
    from ..data.decks import parse_decklist
    from ..parser import parse_card

    deck = parse_decklist(decklist, db, name="hero")
    inert = []
    # The commander counts: an inert commander is the loudest finding a run
    # can produce, and it would be absurd to leave it out of the list.
    for entry in list(deck.entries) + list(deck.commanders):
        card = getattr(entry, "card", entry)
        if card is None:
            continue
        parsed = parse_card(card)
        if parsed.failures:
            inert.append(card.name)
    return sorted(set(inert))


def _print_replay(config: RunConfig, index: int) -> None:
    view = replay_game(config, index)
    print()
    print(f"--- replay of game {index} (seed {view.seed}) ---")
    print(f"{view.turns} turns, winner: {view.winner}")
    print()

    current = -1
    for frame in view.frames:
        if frame.turn != current:
            current = frame.turn
            print(f"\n== Turn {current} ==")
        if frame.kind in ("event", "info"):
            continue
        indent = "  " * (frame.depth + 1)
        print(f"{indent}[{frame.step.lower()}] {frame.text}")

    print()
    print(view.final_board)


if __name__ == "__main__":
    raise SystemExit(main())
