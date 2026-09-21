"""Players and their zones (CR 102, 104, 400).

A player owns three zones - library, hand, and graveyard - while the
battlefield, stack, exile, and command zone are shared and live on the Game
(CR 403.1, 405.1, 406.1, 408.1). Splitting them this way is not cosmetic: it is
what makes "each player shuffles their library" and "exile all creatures"
naturally different operations.

Zone contents are lists of ``ObjectId`` rather than objects. The Game owns the
one true object table, so a card can never end up in two zones because two
lists disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..kernel.enums import LossReason, Zone
from ..kernel.ids import NO_OBJECT, ObjectId, PlayerId
from .cr106_mana import ManaPool

#: CR 903.7: a Commander game starts each player at 40 life.
COMMANDER_STARTING_LIFE = 40
#: CR 903.10a: 21 or more combat damage from a single commander is lethal.
#: The state-based action that reads it is CR 704.6c.
COMMANDER_DAMAGE_THRESHOLD = 21
#: CR 704.5c: ten or more poison counters.
POISON_THRESHOLD = 10
#: CR 103.5 / 402.2: seven cards, and a maximum hand size of seven.
STARTING_HAND_SIZE = 7
DEFAULT_MAX_HAND_SIZE = 7


#: CR 702.183: speed never exceeds this.
MAX_SPEED = 4


@dataclass(slots=True)
class Player:
    """One seat at the table."""

    id: PlayerId
    name: str = ""

    life: int = COMMANDER_STARTING_LIFE
    poison: int = 0
    energy: int = 0
    experience: int = 0
    #: CR 702.183 / 106.13: speed, from Aetherdrift. Zero means the player has
    #: not started their engines; once started it runs 1..4 and never
    #: decreases. "Max speed" abilities function only at 4.
    speed: int = 0
    #: CR 702.179d: speed increases at most once each turn, so the game has to
    #: remember whether it already has this turn.
    speed_increased_this_turn: bool = False

    @property
    def at_max_speed(self) -> bool:
        return self.speed >= MAX_SPEED

    def start_engines(self) -> bool:
        """CR 702.179a: if this player has no speed, it becomes 1.

        Idempotent, and deliberately so: several permanents on the same board
        each say "Start your engines!", and the second one must not reset or
        advance anything.
        """
        if self.speed == 0:
            self.speed = 1
            return True
        return False

    def increase_speed(self) -> bool:
        """CR 702.179d: +1, at most once each turn, never past the maximum.

        Returns whether it actually moved, so the caller can emit an event
        only when something changed.
        """
        if self.speed == 0 or self.speed >= MAX_SPEED:
            return False
        if self.speed_increased_this_turn:
            return False
        self.speed += 1
        self.speed_increased_this_turn = True
        return True
    #: CR 701.46: how deep into a dungeon this player is. Which dungeon and
    #: what each room does is card text; this is only the position.
    dungeon_room: int = 0
    rad: int = 0

    # -- owned zones (CR 401, 402, 404) -------------------------------------
    #: Ordered, and the order is secret. Index 0 is the top.
    library: list[ObjectId] = field(default_factory=list)
    hand: list[ObjectId] = field(default_factory=list)
    #: Ordered (CR 404.3), because some effects care which card went in first.
    #: Index -1 is the most recently added.
    graveyard: list[ObjectId] = field(default_factory=list)

    mana_pool: ManaPool = field(default_factory=ManaPool)

    # -- per-turn state -----------------------------------------------------
    #: CR 305.2: one land per turn, unless an effect says otherwise.
    lands_played: int = 0
    max_lands: int = 1
    max_hand_size: int = DEFAULT_MAX_HAND_SIZE
    #: CR 121.1: how many cards this player has drawn this turn, for effects
    #: that care, and for the "draws a card" trigger. Each one is a separate
    #: draw (CR 121.2), which is why this is a count and not a flag.
    cards_drawn_this_turn: int = 0
    #: Set when a draw from an empty library is attempted. The loss happens as
    #: a state-based action later (CR 704.5b), not immediately.
    attempted_draw_from_empty_library: bool = False
    #: CR 119.3: gaining and losing life adjusts the life total, and some
    #: effects care how much of each happened this turn.
    life_gained_this_turn: int = 0
    life_lost_this_turn: int = 0

    # -- Commander (CR 903) -------------------------------------------------
    #: The player's commanders. One card, or two under a partner variant.
    #: They begin in the command zone (CR 903.6).
    #:
    #: CR 903.3 makes the designation an attribute of the *card*, not of the
    #: object representing it, so these ids are identities rather than live
    #: objects: a commander that changes zones becomes a new object, and
    #: ``Game.commander_identity`` maps that object back to the id recorded
    #: here. Both dictionaries below are keyed the same way, which is what
    #: lets a tax and a damage clock survive the commander dying.
    commanders: tuple[ObjectId, ...] = ()
    #: CR 903.8: each commander costs {2} more for each previous time it was
    #: cast *from the command zone*, tracked per commander.
    commander_casts: dict[ObjectId, int] = field(default_factory=dict)
    #: CR 903.10a: combat damage taken from each individual commander, keyed
    #: by the commander's *original* object id so it survives the commander
    #: changing zones. Per commander, never pooled: two commanders dealing 20
    #: each kill nobody.
    commander_damage: dict[ObjectId, int] = field(default_factory=dict)

    # -- designations -------------------------------------------------------
    is_monarch: bool = False
    has_initiative: bool = False
    has_city_blessing: bool = False
    ring_tempted_count: int = 0

    # -- game status --------------------------------------------------------
    has_lost: bool = False
    has_won: bool = False
    loss_reason: LossReason | None = None
    #: Turn number the player left the game, for statistics.
    left_on_turn: int = -1
    #: CR 104.3h and friends: effects can stop a player winning or losing.
    cannot_lose: bool = False
    cannot_win: bool = False

    # -- zone access --------------------------------------------------------

    def zone(self, zone: Zone) -> list[ObjectId]:
        """The list backing one of this player's owned zones."""
        if zone is Zone.LIBRARY:
            return self.library
        if zone is Zone.HAND:
            return self.hand
        if zone is Zone.GRAVEYARD:
            return self.graveyard
        raise ValueError(f"{zone.name} is not a player-owned zone")

    @property
    def library_size(self) -> int:
        return len(self.library)

    @property
    def hand_size(self) -> int:
        return len(self.hand)

    # -- life (CR 119) ------------------------------------------------------

    def gain_life(self, amount: int) -> int:
        """Gain life, returning the amount actually gained.

        CR 119.9: gaining 0 life is not a life gain event, so "whenever you
        gain life" triggers see nothing. CR 107.1b keeps the other half of the
        guard honest - a negative gain is not a loss, it is a number the game
        cannot use, and zero is used instead.
        """
        if amount <= 0:
            return 0
        self.life += amount
        self.life_gained_this_turn += amount
        return amount

    def lose_life(self, amount: int) -> int:
        """Lose life (CR 119.3). CR 107.1b: a negative loss is zero, not a gain."""
        if amount <= 0:
            return 0
        self.life -= amount
        self.life_lost_this_turn += amount
        return amount

    # -- Commander helpers --------------------------------------------------

    def commander_tax(self, commander: ObjectId) -> int:
        """Extra generic mana for casting a commander (CR 903.8).

        {2} for each previous cast *from the command zone*. Casting a commander
        from hand or graveyard does not increase the tax, which is why this is
        keyed on that specific event.
        """
        return 2 * self.commander_casts.get(commander, 0)

    def record_commander_cast(self, commander: ObjectId) -> None:
        self.commander_casts[commander] = self.commander_casts.get(commander, 0) + 1

    def take_commander_damage(self, commander: ObjectId, amount: int) -> int:
        if amount <= 0:
            return 0
        total = self.commander_damage.get(commander, 0) + amount
        self.commander_damage[commander] = total
        return total

    @property
    def lethal_commander_damage(self) -> ObjectId:
        """The commander that has dealt 21+ damage to this player, if any.

        CR 903.10a states the rule; CR 704.6c is the state-based action that
        acts on it, so this only reports and never itself ends the game.
        """
        for commander, amount in self.commander_damage.items():
            if amount >= COMMANDER_DAMAGE_THRESHOLD:
                return commander
        return NO_OBJECT

    # -- turn reset ---------------------------------------------------------

    def begin_turn(self) -> None:
        """Reset per-turn counters. Called during this player's untap step."""
        self.lands_played = 0
        self.cards_drawn_this_turn = 0
        self.life_gained_this_turn = 0
        self.life_lost_this_turn = 0

    @property
    def is_active_in_game(self) -> bool:
        return not self.has_lost

    def __str__(self) -> str:
        status = "" if self.is_active_in_game else f" (out: {self.loss_reason.name})"
        return f"{self.name or f'P{self.id}'}: {self.life} life{status}"
