"""Trying a card out: the same games, one card changed.

The question this answers is not "is my deck good" - the ordinary run answers
that - but "is *this card* pulling its weight, and would that other one be
better". Two independent 10,000-game runs cannot answer it: the difference
between two decks that differ by one card is usually smaller than the
difference between two runs of the *same* deck, so the noise swamps the
signal and the comparison says whatever it likes.

So every variant runs the same run seed. The seed decides the shuffles, the
mulligans, the opponents' draws and every choice the bots make, which means
the variants share as much of their history as two different decklists can.
It is not a controlled experiment - change a card and the library is a
different library, so the draws diverge from the first shuffle - but it
removes run-to-run variance from the comparison, which is the part that was
drowning the answer.

**How variants are counted.** A *slot* is one card in the deck with one or
more candidates to replace it. Candidates within a slot are always
alternatives - they cannot both replace the same card - so each one is its
own variant.

What differs between the two modes is what happens *across* slots:

``combine=True``
    Slots are independent and their swaps apply together, so the variants
    are the cross product. Ten slots with one candidate each is a single
    fully-swapped deck: "here is my deck with all ten changes". This answers
    "is this list better than that list".

``combine=False``
    Each candidate is tried on its own against the untouched deck, so the
    variants are the sum. Five slots with one candidate each is five decks,
    each differing from the baseline by exactly one card. This answers
    "which of these five swaps is worth making", which the combined mode
    cannot tell you - if the combined deck wins more, you still do not know
    which change did it.

One slot with five candidates gives five variants either way, because
alternatives never combine with each other.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace

from ..data.decks import parse_decklist
from ..data.decks.model import Deck, DeckEntry
from .runner import RunCancelled, RunConfig, run
from .stats import Report, summarize

#: How many variants a plan may have. A run is a run: twenty-five of them at
#: ten thousand games each is a quarter of a million games, and past that the
#: honest answer is "narrow it down" rather than a progress bar that never
#: moves.
MAX_VARIANTS = 24


@dataclass(frozen=True, slots=True)
class Swap:
    """One card out, one card in."""

    original: str
    replacement: str

    def __str__(self) -> str:
        # A plain arrow, not an arrow glyph: this string reaches a Windows
        # console through the crash log and the headless runner, and cp1252
        # cannot encode U+2192 - which turns a label into a traceback.
        return f"{self.original} -> {self.replacement}"


@dataclass(frozen=True, slots=True)
class Slot:
    """A card in the deck, and what might replace it.

    Several candidates in one slot are *alternatives*: they all replace the
    same card, so no variant can contain two of them.
    """

    original: str
    candidates: tuple[str, ...] = ()


@dataclass(slots=True)
class Variant:
    """One deck to run, and what makes it different from the baseline."""

    label: str
    swaps: tuple[Swap, ...]
    decklist: str
    #: Names that could not be swapped in, with the reason. Kept rather than
    #: raised: a plan with one bad candidate should still run the others.
    problems: tuple[str, ...] = ()


@dataclass(slots=True)
class VariantResult:
    """What one variant scored."""

    label: str
    swaps: tuple[str, ...]
    report: Report | None = None
    error: str = ""
    #: The exact run this variant was, kept because a replay rebuilds the
    #: game from the config. Opening a variant's results and then replaying a
    #: game without this would re-simulate the *baseline* deck and show a
    #: game that never happened.
    config: RunConfig | None = None


@dataclass(slots=True)
class LabResult:
    """Every variant, baseline first."""

    variants: list[VariantResult] = field(default_factory=list)
    games: int = 0
    run_seed: int = 0
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Building the decks
# ---------------------------------------------------------------------------


def deck_cards(decklist: str, db) -> list[str]:
    """Every distinct card in a deck, commanders first.

    Commanders are listed too: swapping the commander is a bigger question
    than swapping a Sol Ring and it is exactly the kind of thing this screen
    is for.
    """
    deck = parse_decklist(decklist, db, name="deck")
    names = [card.name for card in deck.commanders]
    seen = set(names)
    for entry in deck.entries:
        if entry.card is not None and entry.card.name not in seen:
            seen.add(entry.card.name)
            names.append(entry.card.name)
    return names


def apply_swaps(decklist: str, swaps: tuple[Swap, ...], db) -> tuple[str, list[str]]:
    """A decklist with each swap applied, plus whatever could not be done.

    Returns the text form rather than a Deck because that is what a run
    takes, and because it is the thing worth showing a person who asks what
    the variant actually was.
    """
    deck = parse_decklist(decklist, db, name="deck")
    problems: list[str] = []

    for swap in swaps:
        incoming = db.lookup(swap.replacement)
        if incoming is None:
            problems.append(f"{swap.replacement!r} is not a card I know")
            continue
        deck, done = _substitute(deck, swap.original, incoming)
        if not done:
            problems.append(f"{swap.original!r} is not in the deck")

    return deck.as_decklist(), problems


def _substitute(deck: Deck, original: str, incoming) -> tuple[Deck, bool]:
    """Replace one named card, keeping its count and its zone.

    The quantity is carried over rather than reset to one: a deck running
    four basics and swapping them for four others is a real thing to ask,
    and silently changing the deck size would make the comparison worthless.
    """
    wanted = original.casefold()

    commanders = list(deck.commanders)
    for index, card in enumerate(commanders):
        if card.name.casefold() == wanted:
            commanders[index] = incoming
            return replace(deck, commanders=tuple(commanders)), True

    entries = list(deck.entries)
    for index, entry in enumerate(entries):
        card = entry.card
        if card is not None and card.name.casefold() == wanted:
            entries[index] = DeckEntry(card=incoming, quantity=entry.quantity)
            return replace(deck, entries=tuple(entries)), True

    return deck, False


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan(
    decklist: str, slots: list[Slot], db, *, combine: bool = True
) -> tuple[list[Variant], list[str]]:
    """Every deck the plan asks for, baseline first.

    See the module docstring for what ``combine`` changes. In short: combined
    slots multiply and separate ones add, and the two answer different
    questions - "is this list better" against "which of these swaps is worth
    making".
    """
    usable = [slot for slot in slots if slot.candidates]
    problems: list[str] = []

    baseline_text, baseline_problems = apply_swaps(decklist, (), db)
    problems += baseline_problems
    variants = [Variant(label="Baseline", swaps=(), decklist=baseline_text)]
    if not usable:
        return variants, problems

    combinations = (
        list(itertools.product(*(slot.candidates for slot in usable)))
        if combine
        else _one_at_a_time(usable)
    )
    if len(combinations) > MAX_VARIANTS:
        problems.append(
            f"that plan needs {len(combinations)} decks, and the limit is "
            f"{MAX_VARIANTS}. "
            + (
                "Combined mode multiplies: several slots with several "
                "candidates each gets very large very fast. Try one slot at "
                "a time, or switch to separate mode, which adds instead."
                if combine
                else "Drop some candidates."
            )
        )
        return variants, problems

    for combination in combinations:
        swaps = tuple(
            Swap(original=slot.original, replacement=pick)
            for slot, pick in zip(usable, combination, strict=True)
            if pick and pick.casefold() != slot.original.casefold()
        )
        if not swaps:
            continue
        text, trouble = apply_swaps(decklist, swaps, db)
        variants.append(
            Variant(
                label=_label(swaps),
                swaps=swaps,
                decklist=text,
                problems=tuple(trouble),
            )
        )

    return variants, problems


def _one_at_a_time(slots: list[Slot]) -> list[tuple]:
    """One combination per candidate, with every other slot left alone.

    Written as full-width rows with empty strings in the untouched slots so
    the caller can zip them against the slot list exactly as it does the
    cross product - one loop, two modes.
    """
    rows: list[tuple] = []
    for index, slot in enumerate(slots):
        for candidate in slot.candidates:
            row = [""] * len(slots)
            row[index] = candidate
            rows.append(tuple(row))
    return rows


def _label(swaps: tuple[Swap, ...]) -> str:
    """A name short enough for a table column and specific enough to trust."""
    if len(swaps) == 1:
        return str(swaps[0])
    if len(swaps) <= 3:
        return ", ".join(swap.replacement for swap in swaps)
    return f"{len(swaps)} swaps"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run_lab(
    variants: list[Variant],
    *,
    opponents: tuple[str, ...],
    games: int,
    run_seed: int,
    card_db_hash: str = "",
    progress=None,
    cancel=None,
) -> LabResult:
    """Run every variant on the same seed.

    ``opponents`` are held fixed for the same reason the seed is: a variant
    that faced different decks is not a comparison, it is two experiments.

    ``cancel`` stops the whole comparison, not just the variant in progress.
    """
    out = LabResult(games=games, run_seed=run_seed)
    done = 0
    total = len(variants) * games

    for variant in variants:
        decklists = (variant.decklist, *opponents)
        config = RunConfig(
            decklists=decklists,
            games=games,
            run_seed=run_seed,
            card_db_hash=card_db_hash,
        )
        offset = done

        def relay(finished: int, _total: int, base: int = offset) -> None:
            if progress is not None:
                progress(base + finished, total)

        try:
            result = run(config, progress=relay, cancel=cancel)
            out.variants.append(
                VariantResult(
                    label=variant.label,
                    swaps=tuple(str(swap) for swap in variant.swaps),
                    report=summarize(result),
                    config=config,
                )
            )
        except RunCancelled:
            # Not a per-variant failure: recording it as one would carry on to
            # the next variant, which is the opposite of stopping.
            raise
        except Exception as exc:  # noqa: BLE001 - reported per variant
            out.variants.append(
                VariantResult(
                    label=variant.label,
                    swaps=tuple(str(swap) for swap in variant.swaps),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        done += games
        if progress is not None:
            progress(done, total)
        out.problems += list(variant.problems)

    return out


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------


def comparison(result: LabResult) -> list[dict]:
    """One row per variant, with its distance from the baseline.

    Deltas are against the first variant, which ``plan`` guarantees is the
    unswapped deck. They are the point of the screen: an absolute win rate
    is a fact about the whole deck and the pod, while the delta is a fact
    about the card.
    """
    rows: list[dict] = []
    base = next((v for v in result.variants if v.report is not None), None)

    for variant in result.variants:
        row: dict = {
            "label": variant.label,
            "swaps": list(variant.swaps),
            "error": variant.error,
        }
        report = variant.report
        if report is None:
            rows.append(row)
            continue

        row.update(
            {
                "games": report.games,
                "win_rate": report.win_rate,
                "stall_rate": report.stall_rate,
                "average_win_turn": report.average_win_turn,
                "commander_turn": _commander_turn(report),
            }
        )
        if base is not None and base.report is not None and variant is not base:
            row["delta_win_rate"] = report.win_rate - base.report.win_rate
            row["delta_win_turn"] = (
                report.average_win_turn - base.report.average_win_turn
            )
            row["delta_stall_rate"] = report.stall_rate - base.report.stall_rate
            row["delta_commander_turn"] = row["commander_turn"] - _commander_turn(
                base.report
            )
        rows.append(row)

    return rows


def _commander_turn(report: Report) -> float:
    """The average round the commander first landed on, or 0 if it never did."""
    total = sum(turn * count for turn, count in report.commander_landed.items())
    landed = sum(report.commander_landed.values())
    return total / landed if landed else 0.0
