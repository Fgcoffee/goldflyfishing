"""The game log, which is also the replay.

Replays are not stored. A run records only its seed, and a replay reconstructs
the game by simulating that seed again with logging switched on. So this module
carries two responsibilities that pull in opposite directions:

* During a 10,000-game run, logging must cost as close to nothing as possible.
  ``GameLog.enabled`` is False and ``record`` returns immediately.
* During a replay, it must capture enough to render a turn-by-turn,
  phase-by-phase view of what happened.

``digest`` exists for the determinism tests: the same seed must produce a
byte-identical log, or replay is a lie.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .enums import Phase, Step
from .ids import NO_PLAYER, PlayerId


@dataclass(frozen=True, slots=True)
class LogEntry:
    turn: int
    phase: Phase
    step: Step
    player: PlayerId
    kind: str
    text: str
    #: Nesting depth: a triggered ability resolving inside another resolution
    #: reads much better indented.
    depth: int = 0

    def __str__(self) -> str:
        indent = "  " * self.depth
        return f"T{self.turn} {self.step.name.lower():<24} {indent}{self.text}"


@dataclass(slots=True)
class GameLog:
    """An ordered record of everything that happened."""

    enabled: bool = False
    entries: list[LogEntry] = field(default_factory=list)
    depth: int = 0
    #: Running hash, maintained even when full logging is off so determinism
    #: can be checked cheaply across a whole run.
    _digest: object = field(default_factory=hashlib.sha256)

    def record(
        self,
        game,
        text: str,
        *,
        kind: str = "info",
        player: PlayerId = NO_PLAYER,
    ) -> None:
        # The hash is always maintained: it is the determinism check, and it
        # must not depend on whether logging happened to be on.
        self._digest.update(f"{game.turn}|{game.step}|{player}|{kind}|{text}\n".encode())
        if not self.enabled:
            return
        self.entries.append(
            LogEntry(
                turn=game.turn,
                phase=game.phase,
                step=game.step,
                player=player,
                kind=kind,
                text=text,
                depth=self.depth,
            )
        )

    def push(self) -> None:
        self.depth += 1

    def pop(self) -> None:
        self.depth = max(0, self.depth - 1)

    def digest(self) -> str:
        """Hash of everything recorded so far.

        Two runs of the same seed must produce the same digest. If they do not,
        something in the engine is reading unordered state or ambient
        randomness, and every replay is suspect.
        """
        return self._digest.hexdigest()

    def text(self) -> str:
        return "\n".join(str(entry) for entry in self.entries)

    def for_turn(self, turn: int) -> list[LogEntry]:
        return [e for e in self.entries if e.turn == turn]

    def __len__(self) -> int:
        return len(self.entries)
