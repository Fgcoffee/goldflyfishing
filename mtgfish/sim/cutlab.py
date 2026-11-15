"""The cut lab: an oversized deck in, the cards to cut out.

A Commander deck is a hundred cards, and the hard part of building one is the
last twenty: a list of a hundred and twenty where every card looks like it
belongs. Testing every card against every other is out of the question - one
card's effect on the win rate is a percent or two, and seeing that through the
noise takes thousands of games per comparison. So the lab works in two steps
and spends its games where the decision actually is.

**Phase 1 - finding the core (``screen_games``).** Lands (and anything the
person marked as core) are always in. The rest of the pool is *unsure*. Many
legal-size decks are drawn from it, each holding a different balanced subset,
and every one of them plays the same seeds against the same opponents - the
swap lab's trick, which takes run-to-run variance out of the comparison. Each
unsure card is then scored on the games of the decks it was in: its win rate
when drawn against its win rate when it was in the library but never drawn.

Cards whose score is *confidently* above the cut line are core and stop being
tested; cards confidently below it are cut. Only the uncertain middle goes on.
How big that middle is depends on how clear the data was, which is the point
of spending more games on phase 1. It is capped, so that a phase 1 too short to
be confident about anything still hands phase 2 a field of the cards nearest
the line rather than the whole pool.

**Phase 2 - head-to-head elimination (``final_games``).** The contenders play
each other: each round builds decks holding as many of the survivors as there
are slots left, on shared seeds again, scores the survivors, and drops the
worst half of the excess. The survivors are re-tested every round, so a card
that looked weak through bad luck in round one gets another chance before it
is gone. When exactly as many survivors remain as there are slots, they are
the deck.

A slice of the phase-2 budget is kept for one ordinary run of the final deck,
which is the report the Results screen shows.

The scoring statistic is the same "drawn against not drawn" the Results
screen ranks cards by. It is not a controlled experiment - a card drawn more in
long games carries some of the long game's result - but every card in a deck
shares that bias, so the *ranking* the lab depends on stands up far better
than any single card's number does.
"""

from __future__ import annotations

import collections
import math
import random
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from .runner import RunCancelled, RunConfig, RunResult

#: How far a card's score must clear the cut line, in its own margins, before
#: phase 1 is willing to call it settled. 1.28 is the one-sided 90% point:
#: confident enough to stop spending games on a card, not so strict that a
#: phase 1 of a few thousand games never settles anything.
CONFIDENCE_Z = 1.28

#: Shrinkage for small samples: a card drawn in only a handful of games has
#: its score pulled towards the pool's average, so that "drawn twice, won
#: twice" cannot outrank a card with a solid record over hundreds of games.
SHRINK_GAMES = 20

#: Fewest games a single variant deck plays. A deck that plays three games
#: tells you nothing on its own, and the per-variant cost of parsing the deck
#: is wasted on it.
MIN_GAMES_PER_VARIANT = 24

#: Most variant decks one phase (or round) builds. More decks spread each
#: card across more company, but past this each one plays too few games.
MAX_VARIANTS = 40


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CutPool:
    """What is being decided: which cards are fixed, which are on trial."""

    commanders: list[str] = field(default_factory=list)
    #: Always in the deck: lands by default, plus whatever was marked core.
    fixed: collections.Counter = field(default_factory=collections.Counter)
    #: The cards being tested, with their quantities.
    unsure: collections.Counter = field(default_factory=collections.Counter)
    deck_size: int = 100
    problems: list[str] = field(default_factory=list)

    @property
    def slots(self) -> int:
        """How many unsure cards fit."""
        return self.deck_size - len(self.commanders) - sum(self.fixed.values())

    @property
    def cuts(self) -> int:
        return sum(self.unsure.values()) - max(0, self.slots)

    def units(self) -> list[str]:
        """One entry per copy, so four of a basic are four separate units."""
        out: list[str] = []
        for name, count in self.unsure.items():
            out.extend([name] * count)
        return out


def _parse(text: str, db):
    from ..data.decks import parse_decklist

    return parse_decklist(text, db, name="deck")


def split_pool(
    decklist: str,
    db,
    *,
    core_text: str | None = None,
    unsure_text: str | None = None,
    deck_size: int = 100,
) -> CutPool:
    """Sort a list into fixed and on-trial cards.

    Simple mode (only ``decklist``): lands are fixed, every other card except
    the commander is on trial. Advanced mode (``core_text`` and/or
    ``unsure_text``): the two boxes say exactly which is which, lands
    included - a person who puts a land in the unsure box wants it tested.
    """
    pool = CutPool(deck_size=deck_size)

    def unresolved(deck) -> None:
        for name in deck.unresolved:
            pool.problems.append(f"{name!r} is not a card I know - left out")

    if core_text is None and unsure_text is None:
        deck = _parse(decklist, db)
        unresolved(deck)
        pool.commanders = [card.name for card in deck.commanders]
        for entry in deck.entries:
            if entry.card is None:
                continue
            target = pool.fixed if entry.card.front.is_land else pool.unsure
            target[entry.card.name] += entry.quantity
        return pool

    core = _parse(core_text or "", db)
    unsure = _parse(unsure_text or "", db)
    unresolved(core)
    unresolved(unsure)
    for card in list(core.commanders) + list(unsure.commanders):
        if card.name not in pool.commanders:
            pool.commanders.append(card.name)
    for entry in core.entries:
        if entry.card is not None:
            pool.fixed[entry.card.name] += entry.quantity
    for entry in unsure.entries:
        if entry.card is None:
            continue
        if entry.card.name in pool.fixed:
            pool.problems.append(
                f"{entry.card.name} is in both boxes - kept as core, not tested"
            )
            continue
        pool.unsure[entry.card.name] += entry.quantity
    return pool


def decklist_text(commanders, *parts: collections.Counter) -> str:
    """A decklist the simulator accepts, from counts of card names."""
    total: collections.Counter = collections.Counter()
    for part in parts:
        total.update(part)
    lines = [f"{count} {name}" for name, count in total.items() if count > 0]
    if commanders:
        lines.append("")
        lines.append("// Commander")
        lines.extend(f"1 {name}" for name in commanders)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CardScore:
    """One card's record across every game of every deck it was in."""

    name: str
    decks: int = 0
    games: int = 0
    drawn: int = 0
    drawn_wins: int = 0
    undrawn: int = 0
    undrawn_wins: int = 0
    #: The shrunk impact, filled in by ``score_cards``. The number ranked on.
    score: float = 0.0

    @property
    def impact(self) -> float:
        drawn = self.drawn_wins / self.drawn if self.drawn else 0.0
        undrawn = self.undrawn_wins / self.undrawn if self.undrawn else 0.0
        return drawn - undrawn

    @property
    def margin(self) -> float:
        """Half-width of a 95% interval on the impact (as ``CardImpact``)."""
        if not self.drawn or not self.undrawn:
            return 1.0
        p1 = self.drawn_wins / self.drawn
        p2 = self.undrawn_wins / self.undrawn
        spread = p1 * (1 - p1) / self.drawn + p2 * (1 - p2) / self.undrawn
        # A floor, so a card that won or lost every game it was drawn in is
        # not treated as known exactly.
        return max(1.96 * math.sqrt(spread), 1.96 * 0.5 / math.sqrt(self.drawn + self.undrawn))

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "impact": self.impact,
            "margin": self.margin,
            "games": self.games,
            "drawn": self.drawn,
            "decks": self.decks,
        }


def score_cards(
    runs: list[tuple[RunResult, collections.Counter]], names, hero: int = 0
) -> dict[str, CardScore]:
    """Score ``names`` over a set of runs, each with the cards its deck held."""
    scores = {name: CardScore(name=name) for name in names}
    for result, included in runs:
        present = [name for name in included if name in scores]
        for name in present:
            scores[name].decks += 1
        for record in result.records:
            won = record.winner == hero
            for name in present:
                score = scores[name]
                score.games += 1
                if name in record.drawn:
                    score.drawn += 1
                    score.drawn_wins += won
                else:
                    score.undrawn += 1
                    score.undrawn_wins += won

    measured = [s for s in scores.values() if s.drawn and s.undrawn]
    mean = sum(s.impact for s in measured) / len(measured) if measured else 0.0
    for score in scores.values():
        weight = score.drawn / (score.drawn + SHRINK_GAMES) if score.undrawn else 0.0
        score.score = mean + weight * (score.impact - mean)
    return scores


# ---------------------------------------------------------------------------
# Building variants
# ---------------------------------------------------------------------------


def variant_plan(games: int) -> tuple[int, int]:
    """How many decks to build for a budget, and how many games each plays."""
    if games <= 0:
        return 0, 0
    per = max(MIN_GAMES_PER_VARIANT, math.ceil(games / MAX_VARIANTS))
    count = max(1, games // per)
    return count, games // count


def balanced_subsets(n: int, take: int, count: int, rng: random.Random) -> list[list[int]]:
    """``count`` subsets of ``range(n)``, each of size ``take``, used evenly.

    Each subset takes the least-used items so far, ties broken at random, so
    every item sits in close to the same number of decks and keeps changing
    company - which is what lets a card's score be about the card rather than
    about whichever cards it happened to share decks with.
    """
    take = max(0, min(take, n))
    usage = [0] * n
    out = []
    for _ in range(count):
        order = sorted(range(n), key=lambda i: (usage[i], rng.random()))
        chosen = sorted(order[:take])
        for index in chosen:
            usage[index] += 1
        out.append(chosen)
    return out


# ---------------------------------------------------------------------------
# Running many decks through one pool
# ---------------------------------------------------------------------------


class Runner:
    """Plays batches of configs through one long-lived worker pool.

    The ordinary ``run`` starts a fresh pool per deck, and every new worker
    reads the card pool's abilities before its first game. That is fine for
    one deck and ruinous for forty variants of one, so the lab keeps one pool
    for its whole life and feeds every variant's games through it.
    """

    def __init__(self, *, workers: int = 0, cancel: threading.Event | None = None):
        from .runner import _default_workers

        self.workers = workers or _default_workers()
        self.cancel = cancel
        self._pool: ProcessPoolExecutor | None = None

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc) -> None:
        self.close(clean=exc[0] is None)

    def close(self, clean: bool = True) -> None:
        from .runner import _stop_pool

        if self._pool is None:
            return
        if clean:
            self._pool.shutdown(wait=True)
        else:
            _stop_pool(self._pool)
        self._pool = None

    def _check(self, done: int, total: int) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise RunCancelled(f"stopped after {done} of {total} games")

    def __call__(
        self, configs: list[RunConfig], progress: Callable[[int], None] | None = None
    ) -> list[RunResult]:
        from .runner import (
            _chunk_of_size,
            _exit_with_parent,
            _play_chunk,
            _quiet_shutdown_noise,
            play_one,
        )

        results = [RunResult(config=config) for config in configs]
        total = sum(config.games for config in configs)
        done = 0

        # Always the pool when there is one, however few the games: playing
        # in-process holds the GIL for the whole game, and the lab's thread
        # lives in the same process as the app - a short elimination round
        # played here starved every other call the page made.
        if self.workers <= 1:
            for slot, config in enumerate(configs):
                for index in range(config.games):
                    self._check(done, total)
                    results[slot].records.append(play_one(config, index, cancel=self.cancel))
                    done += 1
                    if progress:
                        progress(done)
            return results

        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers, initializer=_exit_with_parent
            )
            _quiet_shutdown_noise(self._pool)

        # Chunks grouped by config, so a worker plays a run of one deck's
        # games before moving on and only re-reads the decklist at the seams.
        jobs = []
        for slot, config in enumerate(configs):
            size = max(1, min(64, config.games // (self.workers * 2) or 1))
            for chunk in _chunk_of_size(range(config.games), size):
                jobs.append((slot, config, chunk))

        pending = {
            self._pool.submit(_play_chunk, (config, chunk)): slot
            for slot, config, chunk in jobs
        }
        try:
            while pending:
                finished, _ = wait(list(pending), timeout=0.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    slot = pending.pop(future)
                    records = future.result()
                    results[slot].records.extend(records)
                    done += len(records)
                    if progress:
                        progress(done)
                self._check(done, total)
        except BaseException:
            self.close(clean=False)
            raise

        for result in results:
            result.records.sort(key=lambda record: record.index)
        return results


# ---------------------------------------------------------------------------
# The two phases
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Verdict:
    """What happened to one card, and why."""

    name: str
    outcome: str  # "core" | "kept" | "cut"
    stage: str  # human-readable: "phase 1, confident" etc.
    score: float = 0.0
    margin: float = 0.0
    games: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "stage": self.stage,
            "score": self.score,
            "margin": self.margin,
            "games": self.games,
        }


@dataclass(slots=True)
class CutResult:
    final_decklist: str = ""
    final_config: RunConfig | None = None
    final_result: RunResult | None = None
    verdicts: list[Verdict] = field(default_factory=list)
    screen_scores: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    screen_games: int = 0
    final_games: int = 0
    #: Games actually played, which can be fewer than asked when a phase had
    #: nothing to decide.
    played: int = 0
    problems: list[str] = field(default_factory=list)
    #: For a queue test: the deck before any change, run on the same seeds as
    #: the final deck, so the difference is a paired comparison.
    baseline_result: RunResult | None = None

    @property
    def cuts(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.outcome == "cut"]


Status = Callable[[str], None]


def _configs(decks: list[str], opponents, games: int, seed: int, db_hash: str):
    return [
        RunConfig(
            decklists=(deck, *opponents),
            games=games,
            run_seed=seed,
            card_db_hash=db_hash,
        )
        for deck in decks
    ]


class _Budget:
    """Progress across every phase, as one bar."""

    def __init__(self, total: int, progress):
        self.total = max(1, total)
        self.done = 0
        self.progress = progress

    def relay(self):
        base = self.done

        def tick(done: int) -> None:
            if self.progress:
                self.progress(min(self.total, base + done), self.total)

        return tick

    def spent(self, games: int) -> None:
        self.done += games
        if self.progress:
            self.progress(min(self.total, self.done), self.total)


def _screen(
    pool: CutPool,
    units: list[str],
    games: int,
    *,
    opponents,
    seed: int,
    db_hash: str,
    runner,
    budget: _Budget,
    rng: random.Random,
) -> tuple[dict[str, CardScore], int]:
    """Phase 1: score every unsure unit across balanced random decks."""
    count, per = variant_plan(games)
    if count == 0:
        return {name: CardScore(name=name) for name in set(units)}, 0

    fixed = collections.Counter(pool.fixed)
    decks, holdings = [], []
    for chosen in balanced_subsets(len(units), pool.slots, count, rng):
        held = collections.Counter(units[i] for i in chosen)
        holdings.append(held)
        decks.append(decklist_text(pool.commanders, fixed, held))
    results = runner(_configs(decks, opponents, per, seed, db_hash), budget.relay())
    budget.spent(count * per)
    scores = score_cards(list(zip(results, holdings)), set(units))
    return scores, count * per


def _classify(units, scores, keep: int):
    """Split phase-1 units into confident core, confident cuts and the rest."""
    ranked = sorted(units, key=lambda name: scores[name].score, reverse=True)
    if keep <= 0:
        return [], [], list(ranked), 0.0
    if keep >= len(ranked):
        return list(ranked), [], [], 0.0
    line = (scores[ranked[keep - 1]].score + scores[ranked[keep]].score) / 2

    core, cut, middle = [], [], []
    for name in ranked:
        score = scores[name]
        reach = CONFIDENCE_Z * score.margin / 1.96
        if score.score - reach > line and score.drawn:
            core.append(name)
        elif score.score + reach < line and score.drawn:
            cut.append(name)
        else:
            middle.append(name)
    return core, cut, middle, line


def _eliminate(
    pool: CutPool,
    core: collections.Counter,
    contenders: list[str],
    keep: int,
    games: int,
    *,
    opponents,
    seed: int,
    db_hash: str,
    runner,
    budget: _Budget,
    rng: random.Random,
    status: Status,
) -> tuple[list[str], list[Verdict], list[dict], int]:
    """Phase 2: drop the worst survivors, round after round."""
    survivors = list(contenders)
    if keep <= 0:
        return [], [Verdict(n, "cut", "no room left") for n in survivors], [], 0
    if keep >= len(survivors):
        return survivors, [], [], 0

    # Plan the rounds first, so the budget can be split fairly between them.
    sizes = []
    m = len(survivors)
    while m > keep:
        drop = max(1, math.ceil((m - keep) / 2))
        sizes.append((m, drop))
        m -= drop

    played = 0
    verdicts: list[Verdict] = []
    rounds: list[dict] = []
    fixed = collections.Counter(pool.fixed) + core
    # Every round's games stay in evidence. A survivor's score in round three
    # stands on rounds one and two as well, so the last, closest decisions
    # are made on the most data rather than on the last round's share alone.
    history: list[tuple[RunResult, collections.Counter]] = []
    for number, (size, drop) in enumerate(sizes, start=1):
        left = len(sizes) - number + 1
        share = (games - played) // left
        count, per = variant_plan(share)
        if count == 1 and share >= 2:
            # Head to head needs at least two decks; split the share rather
            # than overspend it.
            count, per = 2, share // 2
        status(
            f"phase 2, round {number} of {len(sizes)}: {size} cards for "
            f"{keep} slots, dropping {drop}"
        )
        if count == 0:
            # Out of games: fall back on the last scores rather than invent.
            count, per = 0, 0
        else:
            decks, holdings = [], []
            for chosen in balanced_subsets(len(survivors), keep, count, rng):
                held = collections.Counter(survivors[i] for i in chosen)
                holdings.append(held)
                decks.append(decklist_text(pool.commanders, fixed, held))
            count = len(decks)
            results = runner(
                _configs(decks, opponents, per, seed, db_hash), budget.relay()
            )
            budget.spent(count * per)
            played += count * per
            history += list(zip(results, holdings))
        scores = score_cards(history, set(survivors))

        ranked = sorted(survivors, key=lambda name: scores[name].score, reverse=True)
        dropped = ranked[len(ranked) - drop:]
        for name in dropped:
            score = scores[name]
            verdicts.append(
                Verdict(
                    name,
                    "cut",
                    f"phase 2, dropped in round {number}",
                    score.score,
                    score.margin,
                    score.games,
                )
            )
        survivors = ranked[: len(ranked) - drop]
        rounds.append(
            {
                "round": number,
                "decks": count,
                "games": count * per,
                "dropped": dropped,
                "scores": [scores[name].as_dict() for name in ranked],
            }
        )
        if number == len(sizes):
            last = scores
    for name in survivors:
        score = last[name]
        verdicts.append(
            Verdict(name, "kept", "phase 2, survived", score.score, score.margin, score.games)
        )
    return survivors, verdicts, rounds, played


def final_share(games: int) -> int:
    """Games kept back from phase 2 for the final deck's own report."""
    if games <= 0:
        return 0
    return min(games, max(50, games // 10))


def run_cut_lab(
    pool: CutPool,
    *,
    opponents: tuple[str, ...],
    screen_games: int,
    final_games: int,
    seed: int = 0,
    db_hash: str = "",
    progress=None,
    status: Status | None = None,
    cancel: threading.Event | None = None,
    runner=None,
) -> CutResult:
    """Both phases and the final run. See the module docstring."""
    status = status or (lambda text: None)
    out = CutResult(screen_games=screen_games, final_games=final_games)
    out.problems += pool.problems
    rng = random.Random(seed)
    units = pool.units()
    keep = pool.slots

    if keep < 0:
        out.problems.append(
            f"the fixed cards alone are {-keep} over {pool.deck_size} - "
            "nothing on trial can fit"
        )
    if pool.cuts <= 0:
        out.problems.append(
            "nothing to cut: the deck already fits in "
            f"{pool.deck_size} cards"
        )

    owned = runner is None
    runner = runner or Runner(cancel=cancel)
    budget = _Budget(screen_games + final_games, progress)
    try:
        core: list[str] = []
        cut: list[str] = []
        contenders = list(units)
        scores: dict[str, CardScore] = {}
        sure: set[str] = set()
        if pool.cuts > 0 and keep > 0:
            status(
                f"phase 1: {len(units)} cards on trial for {keep} slots - "
                "finding the core"
            )
            scores, played = _screen(
                pool, units, screen_games,
                opponents=opponents, seed=seed, db_hash=db_hash,
                runner=runner, budget=budget, rng=rng,
            )
            out.played += played
            core, cut, contenders, _line = _classify(units, scores, keep)
            sure = set(core) | set(cut)
            # Cap the middle: at most as many contenders on each side of the
            # line as there are decisions to make, so a phase 1 too short to be
            # confident hands phase 2 the cards nearest the line, not the pool.
            keep_left = keep - len(core)
            cut_left = len(contenders) - keep_left
            width = min(keep_left, cut_left)
            if width > 0 and len(contenders) > 2 * width:
                above = contenders[:keep_left]
                below = contenders[keep_left:]
                core += above[: keep_left - width]
                cut += below[width:]
                contenders = above[keep_left - width:] + below[:width]
            out.screen_scores = sorted(
                (scores[name].as_dict() for name in set(units)),
                key=lambda row: row["score"],
                reverse=True,
            )
        elif keep <= 0:
            cut, contenders = list(units), []
        else:
            core, contenders = list(units), []

        for outcome, names in (("core", core), ("cut", cut)):
            side = "above" if outcome == "core" else "below"
            for name in names:
                score = scores.get(name) or CardScore(name=name)
                stage = (
                    f"phase 1, confidently {side} the line"
                    if name in sure
                    else f"phase 1, ranked far {side} the line"
                )
                if not scores:
                    stage = "nothing to decide"
                out.verdicts.append(
                    Verdict(name, outcome, stage, score.score, score.margin, score.games)
                )

        reserve = final_share(final_games) if contenders else final_games
        if contenders:
            status(
                f"phase 2: {len(contenders)} contenders for "
                f"{keep - len(core)} slots"
            )
        survivors, verdicts, rounds, played = _eliminate(
            pool,
            collections.Counter(core),
            contenders,
            keep - len(core),
            final_games - reserve,
            opponents=opponents, seed=seed, db_hash=db_hash,
            runner=runner, budget=budget, rng=rng, status=status,
        )
        out.played += played
        out.verdicts += verdicts
        out.rounds = rounds

        chosen = collections.Counter(core) + collections.Counter(survivors)
        out.final_decklist = decklist_text(pool.commanders, pool.fixed, chosen)
        games = final_games - played
        if games > 0:
            status(f"final deck: {games} games for the Results screen")
            config = _configs([out.final_decklist], opponents, games, seed, db_hash)[0]
            out.final_result = runner([config], budget.relay())[0]
            out.final_config = config
            budget.spent(games)
            out.played += games
    finally:
        if owned:
            runner.close(clean=not (cancel is not None and cancel.is_set()))
    return out


# ---------------------------------------------------------------------------
# The recommendations queue: new cards against the deck's weakest
# ---------------------------------------------------------------------------


def weakest_count(queued: int, pool_size: int) -> int:
    """How many of the deck's weakest cards the queue is tested against."""
    return min(pool_size, max(2 * queued, queued + 4))


def queue_pool(
    decklist: str, queue: list[str], db, *, deck_size: int = 100
) -> tuple[CutPool, collections.Counter]:
    """The deck as a pool, and the queued cards that can actually be tested.

    Kept apart from the run because it reads the card database, which belongs
    to whichever thread opened it - the run happens on another.
    """
    base = split_pool(decklist, db, deck_size=deck_size)
    incoming: collections.Counter = collections.Counter()
    for name in queue:
        card = db.lookup(name)
        if card is None:
            base.problems.append(f"{name!r} is not a card I know - left out")
        elif card.name in base.unsure or card.name in base.fixed or card.name in base.commanders:
            base.problems.append(f"{card.name} is already in the deck")
        elif card.name not in incoming:
            incoming[card.name] += 1
    return base, incoming


def run_queue_test(
    base: CutPool,
    incoming: collections.Counter,
    *,
    opponents: tuple[str, ...],
    screen_games: int,
    final_games: int,
    seed: int = 0,
    db_hash: str = "",
    progress=None,
    status: Status | None = None,
    cancel: threading.Event | None = None,
    runner=None,
) -> CutResult:
    """Which cards should make room for the queue, and what it does to winning.

    ``base`` and ``incoming`` come from ``queue_pool``. Phase 1 is one
    ordinary run of the deck as it is, which ranks its own non-land cards. The
    weakest of them then go head to head with the queued cards in phase 2,
    exactly as the cut lab's contenders do; everything stronger stays. The
    final deck is run on the same seeds as the deck was, so the win-rate
    change is a paired comparison.
    """
    status = status or (lambda text: None)
    deck_size = base.deck_size
    out = CutResult(screen_games=screen_games, final_games=final_games)
    out.problems += base.problems
    if not incoming:
        out.problems.append("nothing in the queue to test")
        return out

    owned = runner is None
    runner = runner or Runner(cancel=cancel)
    budget = _Budget(screen_games + final_games, progress)
    try:
        units = base.units()
        status(f"phase 1: ranking the deck's own {len(units)} non-land cards")
        current = decklist_text(base.commanders, base.fixed, base.unsure)
        config = _configs([current], opponents, max(1, screen_games), seed, db_hash)[0]
        baseline = runner([config], budget.relay())[0]
        budget.spent(config.games)
        out.played += config.games
        out.baseline_result = baseline
        scores = score_cards([(baseline, base.unsure)], set(units))
        out.screen_scores = sorted(
            (scores[name].as_dict() for name in set(units)),
            key=lambda row: row["score"],
            reverse=True,
        )

        ranked = sorted(units, key=lambda name: scores[name].score)
        # A deck over the limit has to lose its excess too, so the excess
        # counts as cards to make room for, alongside the queue.
        room = base.slots - len(units)  # negative when the deck is oversized
        excess = max(0, -room)
        weak = weakest_count(sum(incoming.values()) + excess, len(ranked))
        weakest, strong = ranked[:weak], ranked[weak:]
        keep = max(0, weak + room)
        contenders = weakest + list(incoming.elements())

        trial = CutPool(
            commanders=base.commanders,
            fixed=base.fixed + collections.Counter(strong),
            unsure=collections.Counter(contenders),
            deck_size=deck_size,
        )
        for name in strong:
            score = scores[name]
            out.verdicts.append(
                Verdict(name, "core", "phase 1, stronger than the weakest",
                        score.score, score.margin, score.games)
            )

        reserve = final_share(final_games)
        status(
            f"phase 2: {len(incoming)} new card(s) against the deck's "
            f"{weak} weakest"
        )
        survivors, verdicts, rounds, played = _eliminate(
            trial,
            collections.Counter(),
            contenders,
            keep,
            final_games - reserve,
            opponents=opponents, seed=seed, db_hash=db_hash,
            runner=runner, budget=budget, rng=random.Random(seed),
            status=status,
        )
        out.played += played
        out.verdicts += verdicts
        out.rounds = rounds

        out.final_decklist = decklist_text(
            base.commanders, trial.fixed, collections.Counter(survivors)
        )
        games = final_games - played
        if games > 0:
            status(f"final deck: {games} games, same seeds as the deck as it is")
            config = _configs([out.final_decklist], opponents, games, seed, db_hash)[0]
            out.final_result = runner([config], budget.relay())[0]
            out.final_config = config
            budget.spent(games)
            out.played += games
    finally:
        if owned:
            runner.close(clean=not (cancel is not None and cancel.is_set()))
    return out


def paired_change(baseline: RunResult | None, final: RunResult | None, hero: int = 0):
    """Win-rate change over the seeds both runs played.

    Both runs number their games from zero on the same run seed, so game N of
    each shares its seed; comparing only the shared games keeps it paired.
    """
    if baseline is None or final is None:
        return None
    shared = min(len(baseline.records), len(final.records))
    if not shared:
        return None
    before = sum(r.winner == hero for r in baseline.records[:shared]) / shared
    after = sum(r.winner == hero for r in final.records[:shared]) / shared
    return {"games": shared, "before": before, "after": after, "delta": after - before}
