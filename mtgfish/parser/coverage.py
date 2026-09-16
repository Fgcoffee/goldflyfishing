"""Measuring what the parser understands, over the real card pool.

The number that matters is not "how many abilities parsed" but "how many cards
are fully understood", because a card with one unread ability is a card that
will do the wrong thing in a simulation. A 95% ability rate can easily be a 60%
card rate, and the card rate is the one a user feels.

The report is also a work queue: failures are grouped by the token they stopped
at, so the largest cluster is the next grammar rule worth writing.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .compile import parse_card
from .errors import ParseFailure


@dataclass
class CoverageReport:
    """What the parser managed over a set of cards."""

    cards: int = 0
    cards_fully_parsed: int = 0
    cards_with_no_text: int = 0
    abilities: int = 0
    abilities_parsed: int = 0
    failures: list[ParseFailure] = field(default_factory=list)
    #: Cards that failed, by name, so a specific card can be looked up.
    failed_cards: list[str] = field(default_factory=list)

    @property
    def card_percent(self) -> float:
        playable = self.cards - self.cards_with_no_text
        if playable <= 0:
            return 100.0
        return 100.0 * self.cards_fully_parsed / playable

    @property
    def ability_percent(self) -> float:
        if not self.abilities:
            return 100.0
        return 100.0 * self.abilities_parsed / self.abilities

    def stopped_at(self, limit: int = 20) -> list[tuple[str, int]]:
        """The tokens the parser most often could not get past.

        This is the work queue. A cluster of 900 failures on the word
        "instead" is one replacement-effect rule away from being fixed.
        """
        counter = collections.Counter(
            failure.stopped_at for failure in self.failures if failure.stopped_at
        )
        return counter.most_common(limit)

    def by_rule(self) -> list[tuple[str, int]]:
        counter = collections.Counter(failure.rule for failure in self.failures)
        return counter.most_common()

    def render(self) -> str:
        lines = [
            f"Cards:      {self.cards} ({self.cards_with_no_text} with no text)",
            f"Fully read: {self.cards_fully_parsed}  ({self.card_percent:.1f}%)",
            f"Abilities:  {self.abilities_parsed} of {self.abilities}"
            f"  ({self.ability_percent:.1f}%)",
            "",
            "Failures by rule:",
        ]
        lines.extend(f"  {rule or '(none)':12} {count}" for rule, count in self.by_rule())
        lines.append("")
        lines.append("Most common stopping points:")
        lines.extend(f"  {token!r:20} {count}" for token, count in self.stopped_at())
        return "\n".join(lines)


def measure(cards, *, keep_failures: int = 20000) -> CoverageReport:
    """Parse every card and report.

    ``keep_failures`` bounds memory on a full-pool run; the counts stay exact
    either way, because they are accumulated rather than derived from the list.
    """
    report = CoverageReport()

    for card in cards:
        report.cards += 1
        parsed = parse_card(card)

        has_text = any(face.abilities for face in parsed.faces)
        if not has_text:
            report.cards_with_no_text += 1
            continue

        for face in parsed.faces:
            report.abilities += face.total
            report.abilities_parsed += face.understood

        if parsed.fully_parsed:
            report.cards_fully_parsed += 1
        else:
            report.failed_cards.append(parsed.name)
            if len(report.failures) < keep_failures:
                report.failures.extend(parsed.failures)

    return report
