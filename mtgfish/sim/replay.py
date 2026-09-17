"""Reconstructing one game, turn by turn and phase by phase.

Nothing is loaded here - the game is *played again*. The seed determines it
completely, so replaying is exact, and the run stores nothing.

The output is a flat list of frames rather than a tree, because the viewer
scrubs through it linearly and a tree would have to be flattened to do that.
Each frame knows its turn, phase and step, so grouping into a tree for display
is the viewer's business.

Two things the frames alone cannot tell a reader, and which this module works
out for them:

**Whose turn it is, and their own number for it.** The engine's ``turn`` is a
global player-turn counter: in a four-player game the third player's second
turn is turn seven. Nobody thinks in those numbers. Every turn here carries the
seat that took it and that seat's own count of it, so the viewer can say
"Opponent 2, their turn 4" and mean what a person means.

**How loud each entry is.** A game logs far more than a reader wants at once,
and the previous viewer dealt with that by dropping two whole kinds - including
``info``, which is the default kind and carries "all targets illegal; spell is
countered". Filtering by hand is how a replay ends up silently missing the one
line that explained the game. So the levels are named, ordered, and defined
here, where the kinds are: anything not listed is shown by default, and only
the raw event stream has to be asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..rules.ids import NO_PLAYER
from .runner import RunConfig, replay

#: The kinds that carry the story of a game: what was played, what happened to
#: whom, and how it ended. "Key moments" shows these and nothing else.
KEY_KINDS: frozenset[str] = frozenset(
    {
        "setup",
        "turn",
        "cast",
        "land",
        "loss",
        "combat",
        "commander",
        "loop",
        "solved",
        "stall",
        "runaway",
        "restart",
        "mulligan",
        "emblem",
        "monarch",
        "initiative",
        "transform",
        "flip",
    }
)

#: The raw event stream. Tens of thousands of entries saying the same thing as
#: the actions that caused them, and the only kind hidden unless asked for.
NOISE_KINDS: frozenset[str] = frozenset({"event"})

#: Least to most. A kind nobody has classified shows at "normal", so a new kind
#: of entry appears in the viewer rather than vanishing from it.
DETAIL_LEVELS: tuple[str, ...] = ("key", "normal", "everything")


def shown_at(kind: str, level: str) -> bool:
    """Whether an entry of this kind belongs in a view at this detail level."""
    if level == "everything":
        return True
    if kind in NOISE_KINDS:
        return False
    if level == "key":
        return kind in KEY_KINDS
    return True


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
    #: Whose turn this happened on, and that seat's own number for the turn.
    #: Carried on every frame so a viewer can label, filter or group without
    #: having to look the turn up.
    active_player: int = -1
    player_turn: int = 0


@dataclass(slots=True)
class ReplaySeat:
    """One player, as the replay header shows them."""

    id: int
    name: str
    won: bool = False
    #: Populated when they were eliminated: the turn and the reason.
    left_on_turn: int = -1
    loss_reason: str = ""
    turns_taken: int = 0
    #: The seat the run is about. Four seats named P0 to P3 all look alike
    #: until one of them is marked as the deck being measured.
    is_hero: bool = False


@dataclass(slots=True)
class ReplayTurn:
    """One player's turn: who took it, which of theirs it was, what it held."""

    turn: int
    player: int
    player_name: str
    #: This seat's own count of their turns. What a person means by "turn 4".
    #: Zero for the setup block, which is not anybody's turn.
    player_turn: int
    #: The frames belonging to this turn, as a half-open range.
    start: int
    end: int
    #: Shuffling, opening hands, mulligans and turn order - everything logged
    #: before the first turn began. Not a turn, and counting it as one shifted
    #: every later number for whoever happened to be active during setup.
    is_setup: bool = False
    spells_cast: int = 0
    lands_played: int = 0
    #: The active player's position as their turn ended, when the run sampled
    #: one. Absent for the last turn of a game that ended part-way through it.
    life: int | None = None
    lands: int | None = None
    cards_in_hand: int | None = None
    permanents: int | None = None
    board_power: int | None = None

    @property
    def label(self) -> str:
        if self.is_setup:
            return "Before the game"
        return f"{self.player_name} - turn {self.player_turn}"


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
    #: One entry per turn taken, in order. The viewer's table of contents.
    turn_list: list[ReplayTurn] = field(default_factory=list)
    seats: list[ReplaySeat] = field(default_factory=list)
    #: The final board, as text, for the viewer's summary panel.
    final_board: str = ""
    #: Why the game ended, in a few words, when the log said.
    outcome: str = ""

    def at_turn(self, turn: int) -> int:
        """The first frame of a turn, or the closest earlier one."""
        if turn in self.turn_starts:
            return self.turn_starts[turn]
        earlier = [t for t in self.turn_starts if t <= turn]
        return self.turn_starts[max(earlier)] if earlier else 0

    def between(self, start_turn: int, end_turn: int) -> list[ReplayFrame]:
        return [f for f in self.frames if start_turn <= f.turn <= end_turn]

    def visible(self, level: str = "normal") -> list[ReplayFrame]:
        """The frames a viewer at this detail level should show."""
        return [f for f in self.frames if shown_at(f.kind, level)]


def replay_game(config: RunConfig, index: int) -> ReplayView:
    """Replay one game of a run with full logging."""
    record, game = replay(config, index)

    view = ReplayView(
        game_index=index,
        seed=record.seed,
        turns=record.turns,
        winner=record.winner,
        seats=_seats(game, config.hero),
    )
    names = {seat.id: seat.name for seat in view.seats}
    snapshots = {
        (snapshot.player, snapshot.turn): snapshot for snapshot in record.turns_series
    }

    view.frames, view.turn_list, view.turn_starts = build_turns(
        game.log.entries, names, snapshots
    )
    taken = {turn.player: turn.player_turn for turn in view.turn_list}
    for seat in view.seats:
        seat.turns_taken = taken.get(seat.id, 0)

    view.final_board = game.describe_board()
    view.outcome = _outcome(view, game)
    return view


def build_turns(
    entries, names: dict[int, str], snapshots: dict | None = None
) -> tuple[list[ReplayFrame], list[ReplayTurn], dict[int, int]]:
    """Group a log into frames and the turns they belong to.

    Kept separate from ``replay_game`` because this is where the numbering a
    reader sees is decided, and deciding it inside a function that first plays
    a whole game makes it untestable without one.

    A turn ends where the engine's global counter moves, and the seat that took
    it is read off the entry rather than inferred - an opponent drawing on your
    turn is an entry with their id on your turn, and inferring from ``player``
    would open a new turn for them.
    """
    snapshots = snapshots or {}
    frames: list[ReplayFrame] = []
    turns: list[ReplayTurn] = []
    starts: dict[int, int] = {}
    # How many turns each seat has taken so far - the number a person means.
    own_turns: dict[int, int] = {}
    current: ReplayTurn | None = None

    for position, entry in enumerate(entries):
        active = int(entry.active_player)
        if current is None or entry.turn != current.turn:
            if current is not None:
                current.end = position
            # Turn zero is everything logged before the first turn began -
            # shuffling, opening hands, mulligans, turn order. Counting it as
            # somebody's first turn shifted that seat's numbering by one for
            # the whole game, and pulled every one of their end-of-turn
            # snapshots off by one with it.
            setup = entry.turn <= 0
            if not setup:
                own_turns[active] = own_turns.get(active, 0) + 1
            current = ReplayTurn(
                turn=entry.turn,
                player=-1 if setup else active,
                player_name="" if setup else names.get(active, _seat_name(active)),
                player_turn=0 if setup else own_turns[active],
                start=position,
                end=position,
                is_setup=setup,
            )
            turns.append(current)
            starts.setdefault(entry.turn, position)
            if not setup:
                _attach_snapshot(current, snapshots)

        if entry.kind == "cast":
            current.spells_cast += 1
        elif entry.kind == "land":
            current.lands_played += 1

        frames.append(
            ReplayFrame(
                index=position,
                turn=entry.turn,
                phase=entry.phase.name if entry.phase is not None else "",
                step=entry.step.name if entry.step is not None else "",
                player=int(entry.player),
                kind=entry.kind,
                text=entry.text,
                depth=entry.depth,
                active_player=active,
                player_turn=current.player_turn,
            )
        )

    if current is not None:
        current.end = len(frames)
    return frames, turns, starts


def _seat_name(player: int) -> str:
    return "nobody" if player == int(NO_PLAYER) else f"P{player}"


def _seats(game, hero: int) -> list[ReplaySeat]:
    winners = set(int(p) for p in getattr(game, "winners", ()) or ())
    seats = []
    for player in game.players:
        reason = getattr(player, "loss_reason", None)
        seats.append(
            ReplaySeat(
                id=int(player.id),
                name=player.name or f"P{int(player.id)}",
                won=int(player.id) in winners,
                left_on_turn=player.left_on_turn if player.has_lost else -1,
                loss_reason=reason.name if reason else "",
                is_hero=int(player.id) == int(hero),
            )
        )
    return seats


def _attach_snapshot(turn: ReplayTurn, snapshots: dict) -> None:
    """Fill in the end-of-turn position, if the run sampled one.

    Keyed by seat and that seat's own turn number, which is how the observer
    numbers its series too - so the two agree without either having to know
    about the other's indexing. A game that ended part-way through a turn has
    no snapshot for it, and the fields stay ``None`` rather than showing the
    previous turn's numbers as if they were this turn's.
    """
    snapshot = snapshots.get((turn.player, turn.player_turn))
    if snapshot is None:
        return
    turn.life = snapshot.life
    turn.lands = snapshot.lands
    turn.cards_in_hand = snapshot.cards_in_hand
    turn.permanents = snapshot.permanents
    turn.board_power = snapshot.board_power


def _outcome(view: ReplayView, game) -> str:
    """How the game ended, in the words the log already used.

    Read off the log rather than recomputed, so the summary line and the last
    lines of the replay can never disagree.
    """
    for entry in reversed(game.log.entries):
        if entry.kind in ("stall", "runaway", "restart"):
            return entry.text
    if view.winner is None:
        if getattr(game, "loop_draw", False):
            return "a draw: a mandatory loop nothing could break (CR 104.4b)"
        return "nobody won"
    winner = next((s for s in view.seats if s.id == view.winner), None)
    losers = [s for s in view.seats if s.loss_reason]
    how = losers[-1].loss_reason.lower().replace("_", " ") if losers else ""
    name = winner.name if winner else _seat_name(view.winner)
    return f"{name} won" + (f" - last player out to {how}" if how else "")
