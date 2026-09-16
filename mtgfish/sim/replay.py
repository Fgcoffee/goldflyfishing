"""Reconstructing one game, turn by turn and phase by phase.

Nothing is loaded here - the game is *played again*. The seed determines it
completely, so replaying is exact, and the run stores nothing.

The output is a flat list of frames rather than a tree, because the viewer
scrubs through it linearly and a tree would have to be flattened to do that.
Each frame knows its turn, phase and step, so grouping into a tree for display
is the viewer's business.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .runner import RunConfig, replay


@dataclass(slots=True)
class ReplayFrame:
    """One logged moment."""

    index: int
    turn: int
    phase: str
    step: str
    player: int
    kind: str
    text: str
    depth: int = 0


@dataclass(slots=True)
class ReplayView:
    """A whole game, ready to scrub through."""

    game_index: int
    seed: int
    turns: int
    winner: int | None
    frames: list[ReplayFrame] = field(default_factory=list)
    #: Frame index where each turn starts, so "jump to turn 7" is a lookup.
    turn_starts: dict[int, int] = field(default_factory=dict)
    #: The final board, as text, for the viewer's summary panel.
    final_board: str = ""

    def at_turn(self, turn: int) -> int:
        """The first frame of a turn, or the closest earlier one."""
        if turn in self.turn_starts:
            return self.turn_starts[turn]
        earlier = [t for t in self.turn_starts if t <= turn]
        return self.turn_starts[max(earlier)] if earlier else 0

    def between(self, start_turn: int, end_turn: int) -> list[ReplayFrame]:
        return [f for f in self.frames if start_turn <= f.turn <= end_turn]


def replay_game(config: RunConfig, index: int) -> ReplayView:
    """Replay one game of a run with full logging."""
    record, game = replay(config, index)

    view = ReplayView(
        game_index=index,
        seed=record.seed,
        turns=record.turns,
        winner=record.winner,
    )

    for position, entry in enumerate(game.log.entries):
        frame = ReplayFrame(
            index=position,
            turn=entry.turn,
            phase=entry.phase.name if entry.phase is not None else "",
            step=entry.step.name if entry.step is not None else "",
            player=int(entry.player),
            kind=entry.kind,
            text=entry.text,
            depth=entry.depth,
        )
        view.frames.append(frame)
        view.turn_starts.setdefault(entry.turn, position)

    view.final_board = game.describe_board()
    return view
