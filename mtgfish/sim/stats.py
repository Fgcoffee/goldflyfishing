"""Turning a run into the report.

Four questions the brief asked, and how each is answered:

**"Do I win?"** Win rate, and the reason. A stall-out is reported separately
from a loss, because a deck that never loses and never wins has a different
problem from one that loses.

**"When does my commander land?"** A histogram, not a mean. The mean of a
distribution with a long tail of "never" is meaningless, and "never" is the
number that matters.

**"Which cards matter?"** Win rate when drawn minus win rate when not drawn.
At n=10,000 this is the cleanest impact signal available without attributing
causation the simulation cannot establish - and it is honest about small
samples rather than ranking a card seen four times at the top.

**"What do I lose to?"** Every time an opponent's effect takes one of your
objects, the pair is recorded. The aggregate names the cards you actually
lose presence to, which is usually not the ones you expect.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field

from .records import GameRecord, WinReason
from .runner import RunResult

#: A card seen fewer times than this has an impact number too noisy to rank.
#: It is still reported - with its sample size - because "I never draw it" is
#: itself a finding, but it is kept out of the ordering.
MIN_SAMPLE = 30


@dataclass(slots=True)
class CardImpact:
    """One card's measured effect on winning."""

    name: str
    drawn: int
    drawn_wins: int
    not_drawn: int
    not_drawn_wins: int
    cast: int = 0
    average_cast_turn: float = 0.0
    opening_hand: int = 0

    @property
    def win_rate_drawn(self) -> float:
        return self.drawn_wins / self.drawn if self.drawn else 0.0

    @property
    def win_rate_not_drawn(self) -> float:
        return self.not_drawn_wins / self.not_drawn if self.not_drawn else 0.0

    @property
    def impact(self) -> float:
        """The difference the card being in the game makes to winning."""
        return self.win_rate_drawn - self.win_rate_not_drawn

    @property
    def reliable(self) -> bool:
        return self.drawn >= MIN_SAMPLE and self.not_drawn >= MIN_SAMPLE

    @property
    def margin(self) -> float:
        """A 95% interval half-width on the difference of two proportions.

        Reported so a 12% impact from 40 games is not read as the same finding
        as a 12% impact from 4,000. Normal approximation, which at these
        sample sizes is close enough and does not need scipy.
        """
        if not self.drawn or not self.not_drawn:
            return 1.0
        first = self.win_rate_drawn * (1 - self.win_rate_drawn) / self.drawn
        second = (
            self.win_rate_not_drawn * (1 - self.win_rate_not_drawn) / self.not_drawn
        )
        return 1.96 * math.sqrt(first + second)


@dataclass(slots=True)
class RemovalTarget:
    """A card of yours that keeps getting answered."""

    victim: str
    losses: int
    by_source: collections.Counter = field(default_factory=collections.Counter)
    by_method: collections.Counter = field(default_factory=collections.Counter)

    @property
    def worst_source(self) -> tuple[str, int]:
        if not self.by_source:
            return ("", 0)
        return self.by_source.most_common(1)[0]


@dataclass(slots=True)
class TurnSeries:
    """One metric over time, per player, averaged across the run.

    Each point carries the game indices behind it so a click on the chart can
    open one of them. Bounded, because a point averaged over 10,000 games does
    not need 10,000 clickable examples to be useful.
    """

    metric: str
    player: int
    turns: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    samples: list[int] = field(default_factory=list)
    examples: list[list[int]] = field(default_factory=list)
    #: For each point, the game whose value at that turn is closest to the
    #: average, and what that game actually had.
    #:
    #: A plotted point is a *mean*. There may be no game at all in which you
    #: had 14 mana on turn 4 - that is what averaging does - so opening "the
    #: game where that happened" is a promise the data cannot keep. The
    #: nearest game can be opened honestly, as long as the interface says
    #: that is what it is.
    representative: list[int] = field(default_factory=list)
    representative_value: list[float] = field(default_factory=list)


#: How many games behind each chart point stay clickable.
EXAMPLES_PER_POINT = 20

METRICS = (
    "life",
    "lands",
    "mana_available",
    "cards_in_hand",
    "permanents",
    "board_power",
    "creatures",
)


@dataclass(slots=True)
class Report:
    """Everything a run measured."""

    games: int = 0
    hero: int = 0
    #: How many players were at the table, so player-turns can be reported as
    #: rounds - which is the unit the turn cap and every human are in.
    seats: int = 4
    wins: int = 0
    stalls: int = 0
    win_turns: list[int] = field(default_factory=list)
    win_reasons: collections.Counter = field(default_factory=collections.Counter)
    loss_reasons: collections.Counter = field(default_factory=collections.Counter)
    commander_landed: collections.Counter = field(default_factory=collections.Counter)
    #: Which games are behind each bar of the commander histogram. Unlike the
    #: averaged curves, a bar here *is* a set of games - "the games where the
    #: commander landed on turn four" - so opening one of them is exactly what
    #: the bar says, and clicking is honest without qualification.
    commander_landed_games: dict = field(default_factory=dict)
    commander_never: int = 0
    cards: list[CardImpact] = field(default_factory=list)
    removal: list[RemovalTarget] = field(default_factory=list)
    series: list[TurnSeries] = field(default_factory=list)
    #: Game indices grouped by outcome, so "show me a game I won on turn 7"
    #: is a lookup rather than a search.
    by_win_turn: dict[int, list[int]] = field(default_factory=dict)
    #: Games stopped by the action budget, and what was looping in them. A
    #: runaway is a bug in a card rather than a fact about the deck, so it is
    #: reported separately instead of being folded into the stall rate where
    #: it would look like a property of the list.
    runaways: collections.Counter = field(default_factory=collections.Counter)
    #: Every loop noticed across the run, by what was done and what it was.
    loops: collections.Counter = field(default_factory=collections.Counter)
    #: Games that ended in a draw on a mandatory loop (CR 104.4b), counted
    #: apart from stall-outs because they are a different thing entirely.
    loop_draws: int = 0
    unparsed_cards: list[str] = field(default_factory=list)
    digest_mismatches: list[int] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def stall_rate(self) -> float:
        return self.stalls / self.games if self.games else 0.0

    @property
    def average_win_turn(self) -> float:
        return sum(self.win_turns) / len(self.win_turns) if self.win_turns else 0.0

    def top_cards(self, limit: int = 20) -> list[CardImpact]:
        ranked = [card for card in self.cards if card.reliable]
        ranked.sort(key=lambda card: card.impact, reverse=True)
        return ranked[:limit]

    def worst_cards(self, limit: int = 10) -> list[CardImpact]:
        ranked = [card for card in self.cards if card.reliable]
        ranked.sort(key=lambda card: card.impact)
        return ranked[:limit]


def summarize(result: RunResult, *, mismatches: list[int] | None = None) -> Report:
    """Aggregate a run."""
    hero = result.config.hero
    report = Report(games=len(result.records), hero=hero)
    report.seats = len(result.config.decklists) or 4
    report.unparsed_cards = list(result.unparsed_cards)
    report.digest_mismatches = list(mismatches or [])

    for record in result.records:
        if getattr(record, "runaway", ""):
            report.runaways[record.runaway] += 1
        for loop in getattr(record, "loops", ()):
            report.loops[loop] += 1

    _outcomes(result.records, hero, report)
    _commander(result.records, hero, report)
    report.cards = _card_impact(result.records, hero)
    report.removal = _removal(result.records)
    report.series = _series(result.records)
    return report


def _outcomes(records: list[GameRecord], hero: int, report: Report) -> None:
    for record in records:
        if record.winner is None:
            if getattr(record, "loop_draw", False):
                report.loop_draws += 1
                report.win_reasons[WinReason.LOOP_DRAW.name] += 1
                continue
            report.stalls += 1
            report.win_reasons[WinReason.STALL_OUT.name] += 1
            continue

        if record.winner == hero:
            report.wins += 1
            report.win_turns.append(record.turns)
            report.win_reasons[record.win_reason.name] += 1
            report.by_win_turn.setdefault(record.turns, []).append(record.index)
        else:
            for _, loser, reason in record.eliminations:
                if loser == hero:
                    report.loss_reasons[reason] += 1
                    break


def _commander(records: list[GameRecord], hero: int, report: Report) -> None:
    for record in records:
        turn = record.commander_landed[hero] if record.commander_landed else None
        if turn is None:
            report.commander_never += 1
        else:
            report.commander_landed[turn] += 1
            report.commander_landed_games.setdefault(turn, []).append(record.index)


def _card_impact(records: list[GameRecord], hero: int) -> list[CardImpact]:
    """Win rate when a card was drawn, against when it was not.

    Both halves are needed. A card in every winning game that is also in every
    losing game has no measurable impact, and ranking by "appears in wins"
    would put the deck's basic lands at the top.
    """
    drawn = collections.Counter()
    drawn_wins = collections.Counter()
    cast_counts = collections.Counter()
    cast_turns = collections.Counter()
    opening = collections.Counter()
    names: set[str] = set()

    for record in records:
        won = record.winner == hero
        names.update(record.drawn)
        for name in record.drawn:
            drawn[name] += 1
            if won:
                drawn_wins[name] += 1
        for name in record.opening_hand:
            opening[name] += 1
        for event in record.cast:
            cast_counts[event.name] += 1
            cast_turns[event.name] += event.turn

    total = len(records)
    total_wins = sum(1 for record in records if record.winner == hero)

    out = []
    for name in sorted(names):
        seen = drawn[name]
        seen_wins = drawn_wins[name]
        out.append(
            CardImpact(
                name=name,
                drawn=seen,
                drawn_wins=seen_wins,
                not_drawn=total - seen,
                not_drawn_wins=total_wins - seen_wins,
                cast=cast_counts[name],
                average_cast_turn=(
                    cast_turns[name] / cast_counts[name] if cast_counts[name] else 0.0
                ),
                opening_hand=opening[name],
            )
        )
    return out


def _removal(records: list[GameRecord]) -> list[RemovalTarget]:
    targets: dict[str, RemovalTarget] = {}
    for record in records:
        for event in record.removal:
            target = targets.get(event.victim)
            if target is None:
                target = RemovalTarget(victim=event.victim, losses=0)
                targets[event.victim] = target
            target.losses += 1
            target.by_source[event.source] += 1
            target.by_method[event.how] += 1

    ordered = sorted(targets.values(), key=lambda t: t.losses, reverse=True)
    return ordered


def _series(records: list[GameRecord]) -> list[TurnSeries]:
    """Average each metric per player per turn, keeping example games.

    Averaged over the games that *reached* that turn, not over all games -
    otherwise a metric decays toward zero simply because games ended, which
    would read as the deck falling apart.
    """
    totals: dict[tuple[str, int, int], list[float]] = {}
    examples: dict[tuple[str, int, int], list[int]] = {}

    for record in records:
        for snapshot in record.turns_series:
            for metric in METRICS:
                key = (metric, snapshot.player, snapshot.turn)
                bucket = totals.setdefault(key, [0.0, 0.0])
                bucket[0] += getattr(snapshot, metric)
                bucket[1] += 1
                seen = examples.setdefault(key, [])
                if len(seen) < EXAMPLES_PER_POINT:
                    seen.append(record.index)

    means = {
        key: (total / count if count else 0.0)
        for key, (total, count) in totals.items()
    }

    # Second pass: for each point, the game closest to the mean. Done here
    # rather than while accumulating because the mean is not known until every
    # game has been seen.
    nearest: dict[tuple[str, int, int], tuple[float, int, float]] = {}
    for record in records:
        for snapshot in record.turns_series:
            for metric in METRICS:
                key = (metric, snapshot.player, snapshot.turn)
                value = float(getattr(snapshot, metric))
                distance = abs(value - means[key])
                best = nearest.get(key)
                # Ties go to the lower game index so a rerun of the same seed
                # opens the same game.
                if best is None or (distance, record.index) < (best[0], best[1]):
                    nearest[key] = (distance, record.index, value)

    grouped: dict[tuple[str, int], TurnSeries] = {}
    for (metric, player, turn), (total, count) in sorted(totals.items()):
        series = grouped.get((metric, player))
        if series is None:
            series = TurnSeries(metric=metric, player=player)
            grouped[(metric, player)] = series
        key = (metric, player, turn)
        series.turns.append(turn)
        series.values.append(means[key])
        series.samples.append(int(count))
        series.examples.append(examples[key])
        _distance, index, value = nearest.get(key, (0.0, -1, 0.0))
        series.representative.append(index)
        series.representative_value.append(value)

    return [grouped[key] for key in sorted(grouped)]


def render(report: Report) -> str:
    """The report as text, for the CLI and for eyeballing before the GUI."""
    lines = [
        f"Games:      {report.games}",
        f"Win rate:   {report.win_rate:.1%}  ({report.wins} wins)",
        f"Stall-outs: {report.stall_rate:.1%}  ({report.stalls})",
    ]
    if report.win_turns:
        # Both forms, because they answer different questions. The engine
        # counts player-turns; a player thinks in rounds around the table.
        rounds = report.average_win_turn / max(1, report.seats)
        lines.append(
            f"Avg win turn: {report.average_win_turn:.1f} player-turns"
            f"  (~round {rounds:.1f})"
        )

    lines.append("")
    lines.append("How games ended:")
    for reason, count in report.win_reasons.most_common():
        lines.append(f"  {reason.lower().replace('_', ' '):20} {count:6}")

    # The same two notices the Results screen shows above its charts. A
    # headless run printed neither, so an infinite that won - or a card that
    # looped and was stopped - was invisible outside the window.
    if report.loops or report.loop_draws:
        lines.append("")
        lines.append(
            "Infinite loops (after eight identical cycles the result is applied "
            "directly - as far as it kills, never past its fuel):"
        )
        if report.loop_draws:
            lines.append(f"  {report.loop_draws} game(s) drawn on a mandatory loop")
        for loop, count in report.loops.most_common(10):
            lines.append(f"  {count:5} x  {loop[:110]}")
    if report.runaways:
        lines.append("")
        lines.append("Games stopped as runaways (a card looping without a loop to shortcut):")
        for name, count in report.runaways.most_common(10):
            lines.append(f"  {count:5} x  {name[:110]}")

    lines.append("")
    landed = sum(report.commander_landed.values())
    lines.append(
        f"Commander cast: {landed} of {report.games} games"
        f"  (never: {report.commander_never})"
    )
    if report.commander_landed:
        common = sorted(report.commander_landed.items())[:12]
        lines.append("  by turn: " + "  ".join(f"t{t}:{n}" for t, n in common))

    lines.append("")
    lines.append("Cards that most change the win rate:")
    ranked = report.top_cards(10)
    if ranked:
        for card in ranked:
            lines.append(
                f"  {card.name[:32]:32} {card.impact:+.1%} +/-{card.margin:.1%}"
                f"  (drawn {card.drawn}, cast {card.cast})"
            )
    else:
        # An empty table looks like a bug, so it explains itself. The usual
        # cause is a deck with few distinct cards: impact is measured by
        # comparing games where a card was drawn against games where it was
        # not, and a card in 25 copies is drawn in every game.
        lines.append(
            f"  (nothing ranked: impact needs {MIN_SAMPLE} games with the card "
            "and " + f"{MIN_SAMPLE} without, and no card in this deck varies "
            "that much)"
        )
        near = sorted(report.cards, key=lambda c: c.drawn, reverse=True)[:5]
        for card in near:
            lines.append(
                f"    {card.name[:30]:30} drawn in {card.drawn}/{report.games}"
                f", cast {card.cast}"
            )

    if report.removal:
        lines.append("")
        lines.append(
            "What you lose presence to (only losses with an identifiable "
            "culprit - creatures traded in combat are not counted):"
        )
        for target in report.removal[:10]:
            source, count = target.worst_source
            lines.append(
                f"  {target.victim[:28]:28} lost {target.losses:5}"
                f"  most often to {source[:24]} ({count})"
            )

    if report.unparsed_cards:
        lines.append("")
        lines.append(
            f"Inert cards in the deck ({len(report.unparsed_cards)}) - these did "
            "nothing, so the numbers above understate them:"
        )
        for name in report.unparsed_cards[:15]:
            lines.append(f"  {name}")

    if report.digest_mismatches:
        lines.append("")
        lines.append(
            "WARNING: replays do not reproduce these games: "
            f"{report.digest_mismatches}. The statistics are still valid; the "
            "replay viewer would show a different game."
        )

    return "\n".join(lines)
