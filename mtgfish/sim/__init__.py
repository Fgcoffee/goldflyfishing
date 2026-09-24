"""Running many games and reporting on them.

The simulator's contract with the rest of the program is small: give it deck
lists and a run seed, get back a report. Everything expensive - the card
database, the parser, the engine - is rebuilt inside worker processes rather
than shipped across process boundaries.

The design decision that shapes the rest: **replays are regenerated, not
stored**. A 10,000-game run keeps one small record per game and no logs at all.
Clicking a datapoint replays that game's seed with logging on, which
reconstructs it exactly because the engine is deterministic from the seed. That
turns "every datapoint is clickable" from a storage problem into an arithmetic
one.
"""

from __future__ import annotations

from .records import GameRecord, RemovalEvent, TurnSnapshot, WinReason
from .replay import (
    DETAIL_LEVELS,
    ReplayFrame,
    ReplaySeat,
    ReplayTurn,
    ReplayView,
    replay_game,
    shown_at,
)
from .runner import RunConfig, RunResult, replay, run, verify
from .stats import CardImpact, RemovalTarget, Report, TurnSeries, render, summarize

__all__ = [
    "DETAIL_LEVELS",
    "CardImpact",
    "GameRecord",
    "RemovalEvent",
    "RemovalTarget",
    "ReplayFrame",
    "ReplaySeat",
    "ReplayTurn",
    "ReplayView",
    "Report",
    "RunConfig",
    "RunResult",
    "TurnSeries",
    "TurnSnapshot",
    "WinReason",
    "render",
    "replay",
    "replay_game",
    "run",
    "shown_at",
    "summarize",
    "verify",
]
