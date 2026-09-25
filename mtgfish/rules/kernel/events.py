"""Game events: the substrate triggers and replacement effects run on.

Everything that happens in a game is an event. Triggered abilities watch the
event stream (CR 603.2); replacement effects intercept events before they
happen (CR 614.1). Modelling both against one event type is what makes it
possible to add a card's behaviour without touching the engine.

An ``Event`` is one flat struct with a ``kind`` discriminator rather than a
class hierarchy. Two reasons: dispatch on an integer beats isinstance chains in
the hot loop, and a tagged union ports directly to the native implementation
later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from .enums import Zone
from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId


class EventKind(IntEnum):
    """Things that can happen.

    Grouped by CR area. New kinds are appended rather than renumbered, because
    a saved run records the engine version and replays must line up.
    """

    # -- zone changes (CR 400.7, 603.6) ------------------------------------
    ZONE_CHANGE = 0
    ENTERS_BATTLEFIELD = 1
    LEAVES_BATTLEFIELD = 2
    DIES = 3  # Battlefield -> graveyard specifically (CR 700.4)
    PUT_INTO_GRAVEYARD = 4
    EXILED = 5
    RETURNED_TO_HAND = 6
    SHUFFLED_INTO_LIBRARY = 7
    TOKEN_CREATED = 8
    CEASED_TO_EXIST = 9

    # -- the stack (CR 601, 602, 608) --------------------------------------
    CAST_SPELL = 20
    SPELL_RESOLVED = 21
    ABILITY_ACTIVATED = 22
    ABILITY_TRIGGERED = 23
    ABILITY_RESOLVED = 24
    COUNTERED = 25
    FIZZLED = 26  # Countered on resolution for having no legal targets
    TARGETED = 27
    COPIED = 28
    PUT_ON_STACK = 29

    # -- permanents --------------------------------------------------------
    TAPPED = 40
    UNTAPPED = 41
    COUNTER_ADDED = 42
    COUNTER_REMOVED = 43
    ATTACHED = 44
    UNATTACHED = 45
    TRANSFORMED = 46
    TURNED_FACE_UP = 47
    TURNED_FACE_DOWN = 48
    PHASED_OUT = 49
    PHASED_IN = 50
    CONTROL_CHANGED = 51
    SACRIFICED = 52
    DESTROYED = 53
    REGENERATED = 54

    # -- damage and life (CR 119, 120) -------------------------------------
    DAMAGE_DEALT = 60
    COMBAT_DAMAGE_DEALT = 61
    LIFE_GAINED = 62
    LIFE_LOST = 63
    LIFE_SET = 64
    DAMAGE_PREVENTED = 65
    POISON_ADDED = 66
    #: CR 702.179d: a player's speed went up.
    SPEED_INCREASED = 67

    # -- cards -------------------------------------------------------------
    DREW_CARD = 80
    DISCARDED = 81
    MILLED = 82
    REVEALED = 83
    SCRIED = 84
    SURVEILLED = 85
    SEARCHED_LIBRARY = 86
    #: CR 716.2: a Class gained a level. ``amount`` is the new level.
    CLASS_LEVEL_GAINED = 87
    SHUFFLED = 87

    # -- combat (CR 506-511) -----------------------------------------------
    ATTACKERS_DECLARED = 100
    ATTACKS = 101
    BLOCKERS_DECLARED = 102
    BLOCKS = 103
    BECOMES_BLOCKED = 104
    REMOVED_FROM_COMBAT = 105

    # -- turn structure (CR 500) -------------------------------------------
    TURN_BEGAN = 120
    PHASE_BEGAN = 121
    STEP_BEGAN = 122
    STEP_ENDED = 123
    PHASE_ENDED = 124
    TURN_ENDED = 125
    UNTAP_STEP = 126
    UPKEEP = 127
    DRAW_STEP = 128
    END_STEP = 129
    CLEANUP = 130

    # -- players (CR 104, 800.4) -------------------------------------------
    PLAYER_LOST = 140
    PLAYER_WON = 141
    PLAYER_LEFT_GAME = 142
    MANA_ADDED = 143
    MANA_SPENT = 144
    MANA_EMPTIED = 145
    PRIORITY_RECEIVED = 146
    LAND_PLAYED = 147

    # -- Commander (CR 903) -------------------------------------------------
    COMMANDER_CAST = 160
    COMMANDER_MOVED_TO_COMMAND_ZONE = 161
    COMMANDER_DAMAGE_DEALT = 162

    # -- designations and counters on players -------------------------------
    BECAME_MONARCH = 180
    TOOK_INITIATIVE = 181
    ENERGY_GAINED = 182
    EXPERIENCE_GAINED = 183
    DAY_NIGHT_CHANGED = 184
    RING_TEMPTED = 185
    EXPLORED = 187
    DUNGEON_VENTURED = 186
    #: CR 700.11: a permanent card was put into its owner's graveyard.
    DESCENDED = 330
    #: CR 700.13: a player committed a crime.
    CRIME_COMMITTED = 331
    #: CR 700.14: a player's mana spent on spells this turn reached ``amount``.
    EXPENDED = 332


#: Events that mean a permanent left the battlefield in some form. Abilities
#: that trigger on these use last-known information (CR 603.6e, 608.2g).
LEAVE_BATTLEFIELD_KINDS = frozenset(
    {
        EventKind.LEAVES_BATTLEFIELD,
        EventKind.DIES,
        EventKind.SACRIFICED,
        EventKind.DESTROYED,
    }
)

#: CR 603.10: the events whose triggers "look back in time" - they are checked
#: against the game as it was *before* the event, not after.
#:
#: The list is exactly the CR's, and it is longer than intuition suggests.
#: Leaving the battlefield is the famous case (603.10a), but phasing out
#: (603.10b), becoming unattached (603.10c) and losing control of a permanent
#: (603.10d) all work the same way - and each is a bug of the same shape if
#: missed, because the ability asks about an object that no longer looks the
#: way it did when the trigger condition was met.
LOOK_BACK_KINDS = LEAVE_BATTLEFIELD_KINDS | frozenset(
    {
        EventKind.PHASED_OUT,
        EventKind.UNATTACHED,
        EventKind.CONTROL_CHANGED,
    }
)


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, or is about to.

    A replacement effect sees the event before it is applied and may return a
    modified copy (CR 614.1); everything else sees it after the fact.
    """

    kind: EventKind

    #: The object the event is about - the permanent that died, the spell cast.
    object_id: ObjectId = NO_OBJECT
    #: The player the event is about - who drew, who lost life.
    player: PlayerId = NO_PLAYER
    #: What caused it. For damage this is the damage source, which matters for
    #: deathtouch, lifelink, and commander damage.
    source: ObjectId = NO_OBJECT
    #: The controller of ``source`` at the time, captured because the source
    #: may have changed control or left the battlefield by the time a trigger
    #: resolves.
    source_controller: PlayerId = NO_PLAYER

    amount: int = 0
    from_zone: Zone | None = None
    to_zone: Zone | None = None

    #: Kind-specific extras, e.g. the counter type for COUNTER_ADDED. A tuple
    #: rather than a dict so events stay hashable and cheap to copy.
    data: tuple = ()

    def with_amount(self, amount: int) -> Event:
        """A copy with a different amount - the common replacement-effect edit."""
        return replace(self, amount=amount)

    def replaced(self, **changes) -> Event:
        return replace(self, **changes)

    @property
    def is_leave_battlefield(self) -> bool:
        return self.kind in LEAVE_BATTLEFIELD_KINDS

    @property
    def looks_back_in_time(self) -> bool:
        """CR 603.10: whether this event's triggers see the pre-event game."""
        return self.kind in LOOK_BACK_KINDS

    def __str__(self) -> str:
        parts = [self.kind.name]
        if self.object_id != NO_OBJECT:
            parts.append(f"obj={self.object_id}")
        if self.player != NO_PLAYER:
            parts.append(f"player={self.player}")
        if self.source != NO_OBJECT:
            parts.append(f"src={self.source}")
        if self.amount:
            parts.append(f"amount={self.amount}")
        if self.from_zone is not None or self.to_zone is not None:
            frm = self.from_zone.name if self.from_zone is not None else "-"
            to = self.to_zone.name if self.to_zone is not None else "-"
            parts.append(f"{frm}->{to}")
        if self.data:
            parts.append(f"data={self.data}")
        return " ".join(parts)
