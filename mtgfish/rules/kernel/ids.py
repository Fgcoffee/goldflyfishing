"""Identity and ordering primitives.

Three distinct kinds of integer identity, kept apart because conflating them is
a classic source of rules bugs:

``ObjectId``
    Identifies an *object* (CR 109.1), not a card. An object that changes zones
    becomes a new object with no memory of the old one (CR 400.7), and so gets
    a fresh ObjectId. This is what makes "exile it, then return it" correctly
    forget counters, auras, and damage.

``Timestamp``
    Orders continuous effects within a layer (CR 613.7). Monotonic and global.
    Permanents receive a timestamp on entering the battlefield, and a *new* one
    when an Aura or Equipment attaches to them (CR 613.7d-f).

``PlayerId``
    A seat at the table. Stable for the whole game even after the player loses,
    because effects may still reference them.
"""

from __future__ import annotations

from typing import NewType

ObjectId = NewType("ObjectId", int)
PlayerId = NewType("PlayerId", int)
Timestamp = NewType("Timestamp", int)

#: Sentinel for "no object". Never allocated to a real object.
NO_OBJECT = ObjectId(0)

#: Sentinel for "no player" (e.g. an emblem with no controller in some effects).
NO_PLAYER = PlayerId(-1)


class IdAllocator:
    """Monotonic id source, owned by the Game.

    Deliberately not global: two games running in the same worker process must
    not share a counter, or their logs would diverge and replay would break.
    """

    __slots__ = ("_next_object", "_next_timestamp")

    def __init__(self) -> None:
        self._next_object = 1
        self._next_timestamp = 1

    def object_id(self) -> ObjectId:
        oid = self._next_object
        self._next_object += 1
        return ObjectId(oid)

    def timestamp(self) -> Timestamp:
        ts = self._next_timestamp
        self._next_timestamp += 1
        return Timestamp(ts)

    @property
    def peek_timestamp(self) -> Timestamp:
        """The timestamp that would be handed out next, without consuming it."""
        return Timestamp(self._next_timestamp)


#: CR 115.4: a target can be a player as well as an object, and both travel
#: through the same "chosen targets" list. Object ids are positive and start at
#: one, so a player is encoded as a negative number and the two never collide.
#: The alternative - a second parallel list of player targets - would have to
#: be threaded through announcement, validation, resolution and the agent
#: protocol, and would be wrong in a different place every time.


def player_target(player_id: int) -> int:
    """Encode a player as a target id."""
    return -(int(player_id) + 1)


def is_player_target(target: int) -> bool:
    return target < 0


def target_player(target: int) -> PlayerId:
    """Decode a player target id."""
    return PlayerId(-target - 1)
