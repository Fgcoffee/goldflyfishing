"""What a simulated game records.

Everything here is chosen so the report the user asked for can be built without
storing the games themselves. A run of 10,000 games keeps aggregate counters
and one small record per game; the games are *regenerated* from their seeds
when someone wants to look at one.

That is why every record carries its ``seed``. A datapoint on a chart is only
clickable if the click can reconstruct the game behind it, and reconstruction
is exact because the engine is deterministic from the seed alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class WinReason(IntEnum):
    """How a game ended.

    "Nobody won" is a real outcome and gets its own value rather than being
    encoded as a missing winner - a 25-turn stall is information about the
    deck, not an absence of information.
    """

    STALL_OUT = 0
    COMBAT_DAMAGE = 1
    COMMANDER_DAMAGE = 2
    NONCOMBAT_DAMAGE = 3
    MILL = 4
    POISON = 5
    ALTERNATE = 6
    LAST_STANDING = 7
    CONCEDED = 8
    #: CR 104.4b: a mandatory infinite loop that nothing could stop. Not a
    #: stall-out - the deck did something, and it was decisive in its way.
    LOOP_DRAW = 9


@dataclass(slots=True)
class TurnSnapshot:
    """One player's position at the end of *their own* turn.

    Sampled on their own turn, and numbered by how many turns they have had,
    rather than by the global player-turn counter. Both parts matter:

    Sampling every player at every player's turn-end measures each of them at
    a different point in their own cycle - one has just untapped and cast, one
    is three turns from untapping - and averaging that across games where the
    hero sat in different seats smears the result into noise.

    Numbering by the player's own turns is what a human means by "turn 5".
    The global counter would put a four-player game's fifth round at turn 20.
    """

    turn: int
    player: int
    life: int
    lands: int
    #: Mana this player can produce on their turn - every mana source they
    #: control, tapped or not. Not the untapped ones: measured at end of turn
    #: those are whatever is left after casting, which falls as the deck
    #: develops and reads as a mana base going backwards.
    mana_available: int
    cards_in_hand: int
    permanents: int
    board_power: int
    creatures: int


@dataclass(slots=True)
class RemovalEvent:
    """One of your objects leaving because of somebody else.

    This is the raw material for "cards you consistently lost presence to".
    The source matters more than the victim: knowing a deck loses its
    commander a lot is mildly useful, knowing it loses the commander to
    Swords to Plowshares specifically is actionable.
    """

    turn: int
    victim: str
    victim_controller: int
    source: str
    source_controller: int
    how: str


@dataclass(slots=True)
class CardEvent:
    """A card doing something worth attributing."""

    turn: int
    name: str
    controller: int
    kind: str


@dataclass(slots=True)
class GameRecord:
    """One finished game, small enough that 10,000 of them fit in memory.

    Deliberately not a log. The log is 50,000 lines a game and is regenerated
    on demand; this is the summary a chart is built from.
    """

    index: int
    seed: int
    turns: int
    winner: int | None = None
    win_reason: WinReason = WinReason.STALL_OUT
    #: Elimination order, earliest first, with the reason each player lost.
    eliminations: list[tuple[int, int, str]] = field(default_factory=list)

    #: Per player, the turn their commander first resolved. ``None`` if it
    #: never landed, which is itself one of the headline numbers.
    commander_landed: list[int | None] = field(default_factory=list)
    commander_casts: list[int] = field(default_factory=list)
    mulligans: list[int] = field(default_factory=list)

    #: The goldfished player's own cards: what was drawn, and what was cast.
    drawn: set[str] = field(default_factory=set)
    opening_hand: list[str] = field(default_factory=list)
    cast: list[CardEvent] = field(default_factory=list)

    turns_series: list[TurnSnapshot] = field(default_factory=list)
    removal: list[RemovalEvent] = field(default_factory=list)

    #: The engine's log digest. Two runs of one seed must produce the same
    #: value; a mismatch means something read unordered state and every replay
    #: in the run is suspect.
    digest: str = ""

    #: Set when the game was stopped by the action budget rather than played
    #: out - names the action that was looping. A runaway is a bug in a card,
    #: not a fact about the deck, so it is carried all the way to the report
    #: instead of being left in a log that is off by default.
    runaway: str = ""

    #: Every loop noticed this game and what was done about it - shortcut,
    #: stopped, declined, or drawn. A loop that was *stopped* because it
    #: changed nothing is usually a card read wrong, which is why it is kept.
    loops: list[str] = field(default_factory=list)
    loop_draw: bool = False

    @property
    def stalled(self) -> bool:
        return self.winner is None

    def snapshot_for(self, player: int, turn: int) -> TurnSnapshot | None:
        for snapshot in self.turns_series:
            if snapshot.player == player and snapshot.turn == turn:
                return snapshot
        return None
