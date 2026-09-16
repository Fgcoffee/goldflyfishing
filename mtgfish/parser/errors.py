"""What a parse failure is, and why it is a value rather than an exception.

A failed parse is the *normal* outcome for a large fraction of a 37,000-card
pool, so it cannot be exceptional control flow. It is data: what failed, where,
and what was left over. That is what makes the coverage report actionable -
"3,412 abilities failed" is useless, "1,208 of them stopped at the token
``instead``" is a morning's work.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """One ability the parser could not fully read.

    ``consumed`` matters as much as ``reason``: an ability that failed on its
    first token is unrecognised, one that failed on its last is *nearly*
    understood, and the two need completely different work.
    """

    text: str
    reason: str
    #: How many tokens were consumed before giving up.
    consumed: int = 0
    #: The tokens that were left over, which is where the grammar has to grow.
    remaining: tuple[str, ...] = ()
    #: The rule that was being attempted, for tracing.
    rule: str = ""

    @property
    def stopped_at(self) -> str:
        """The first token the parser could not use."""
        return self.remaining[0] if self.remaining else ""

    def __str__(self) -> str:
        where = f" at {self.stopped_at!r}" if self.remaining else ""
        rule = f" [{self.rule}]" if self.rule else ""
        return f"{self.reason}{where}{rule}: {self.text}"


@dataclass(slots=True)
class FailureLog:
    """Failures gathered across a parse, kept in order."""

    failures: list[ParseFailure] = field(default_factory=list)

    def add(self, failure: ParseFailure) -> None:
        self.failures.append(failure)

    def __bool__(self) -> bool:
        return bool(self.failures)

    def __len__(self) -> int:
        return len(self.failures)

    def __iter__(self):
        return iter(self.failures)
