"""Dungeons (CR 309) and venturing into them (CR 701.49).

A dungeon is a card that begins outside the game and is brought into the
command zone by venturing. It is a graph of rooms joined by arrows; its
owner's venture marker sits on one room at a time, and each room carries a
triggered ability that fires as the marker moves into it. Reaching the
bottommost room and letting its ability leave the stack completes the dungeon
and removes it from the game.

The rules name the dungeons a player may use, so their definitions are loaded
by name from the card database - the one place in the engine where a card is
looked up that way. Everything else about them is read from the cards
themselves: the room graph from the oracle text's layout, and each room's
effect by the same parser every other card goes through. A room whose effect
the parser cannot read keeps an UNPARSED effect: the ability still triggers
and still sits on the stack (which is what completion waits for), and does
nothing when it resolves, loudly, rather than something invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, unparsed
from ..kernel.enums import Color, Zone
from ..kernel.events import Event, EventKind
from ..kernel.ids import NO_OBJECT, PlayerId

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


#: The dungeon cards the rules make available (CR 309.2). Undercity is
#: entered only through the initiative (CR 726.2), which its own text says.
DUNGEON_NAMES = (
    "Lost Mine of Phandelver",
    "Dungeon of the Mad Mage",
    "Tomb of Annihilation",
    "Baldur's Gate Wilderness",
    "Undercity // The Initiative",
)

_COMMAND = frozenset({Zone.COMMAND})

#: "Room Name — effect (Leads to: Next Room, Other Room)". The arrows of the
#: printed card are rendered in oracle text as the parenthetical; the
#: bottommost room has none.
_ROOM_LINE = re.compile(
    r"^(?P<name>[^—]+?)\s+—\s+(?P<effect>.+?)(?:\s*\(Leads to:\s*(?P<next>[^)]*)\))?$"
)
#: CR 701.49d: the line that restricts a dungeon to "venture into [quality]".
_ENTRY_LINE = re.compile(
    r"^You can't enter this dungeon unless you \"venture into (?P<quality>.+?)\.?\"\.?$"
)


@dataclass(frozen=True, slots=True)
class Room:
    """One room of a dungeon: its name (flavour only, CR 309.4b), the effect
    of its room ability, and the rooms its arrows point to, by index."""

    name: str
    effect_text: str
    leads_to: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Dungeon:
    """A dungeon card's definition, as read from its oracle text."""

    name: str
    rooms: tuple[Room, ...]
    #: CR 701.49d: the quality that alone lets a player enter it. Empty for a
    #: dungeon any venture may choose.
    entry_quality: str = ""
    #: The printed face, so the object in the command zone has the card's own
    #: name and type line.
    face: object = None
    oracle_id: str = ""
    #: One room ability per room, in room order (CR 309.4c).
    abilities: tuple[Ability, ...] = ()

    @property
    def bottommost(self) -> int:
        """The one room no arrow leads away from."""
        ends = [index for index, room in enumerate(self.rooms) if not room.leads_to]
        return ends[0] if len(ends) == 1 else -1

    @property
    def is_mappable(self) -> bool:
        """Whether the oracle text gave a usable room graph.

        One bottommost room, and every room reachable from the topmost. A
        dungeon whose text lists rooms with no arrows at all - Baldur's Gate
        Wilderness is printed that way - has no graph to follow, and is never
        offered rather than being walked in an order nobody printed.
        """
        if not self.rooms or self.bottommost < 0:
            return False
        seen = {0}
        frontier = [0]
        while frontier:
            for nxt in self.rooms[frontier.pop()].leads_to:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return len(seen) == len(self.rooms)

    def has_quality(self, quality: str) -> bool:
        """CR 701.49d: whether this dungeon has the named quality - its name,
        or one of its subtypes (Undercity is both)."""
        wanted = quality.lower()
        if self.name.lower() == wanted:
            return True
        type_line = getattr(self.face, "type_line", None)
        subtypes = getattr(type_line, "subtypes", ()) or ()
        return any(str(subtype).lower() == wanted for subtype in subtypes)


# ---------------------------------------------------------------------------
# Reading dungeon cards
# ---------------------------------------------------------------------------


def parse_dungeon_text(text: str) -> tuple[tuple[Room, ...], str]:
    """The rooms and entry restriction printed in a dungeon's oracle text.

    Returns the rooms in printed order, the topmost first, with each room's
    arrows resolved to room indices; and the CR 701.49d quality, if any.
    A line that is neither a room nor the entry restriction is not guessed at.
    """
    raw: list[tuple[str, str, tuple[str, ...]]] = []
    quality = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = _ENTRY_LINE.match(line)
        if entry is not None:
            quality = entry.group("quality").strip()
            continue
        room = _ROOM_LINE.match(line)
        if room is None:
            continue
        leads = tuple(
            part.strip() for part in (room.group("next") or "").split(",") if part.strip()
        )
        raw.append((room.group("name").strip(), room.group("effect").strip(), leads))

    index = {name: position for position, (name, _, _) in enumerate(raw)}
    rooms = tuple(
        Room(
            name=name,
            effect_text=effect,
            leads_to=tuple(index[target] for target in leads if target in index),
        )
        for name, effect, leads in raw
    )
    return rooms, quality


def parse_room_effect(text: str) -> tuple[Effect, ...]:
    """A room ability's effect, read by the ordinary effect grammar.

    CR 309.4c: the effect is printed on the card like any other ability's.
    Anything the grammar does not fully consume stays UNPARSED.
    """
    from ...parser.clauses import parse_effects
    from ...parser.tokens import Stream

    stream = Stream.of(text)
    try:
        effects = parse_effects(stream)
    except Exception:  # noqa: BLE001 - an unreadable room must not stop the game
        effects = None
    if (
        effects is None
        or not stream.done
        or any(node.is_unparsed for effect in effects for node in effect.walk())
    ):
        return (unparsed(text),)
    return tuple(effects)


def room_ability(position: int, room: Room) -> Ability:
    """CR 309.4c: "When you move your venture marker into this room, [effect]."

    The trigger condition is not printed on the card; it is the same for
    every room, told apart only by which room it is.
    """
    trigger = TriggerCondition(
        event_kinds=frozenset({EventKind.VENTURE_MARKER_MOVED}),
        functions_in=_COMMAND,
        room=position + 1,
        text="when you move your venture marker into this room",
    )
    return Ability(
        AbilityKind.TRIGGERED,
        effects=parse_room_effect(room.effect_text),
        trigger=trigger,
        functions_in=_COMMAND,
        text=f"{room.name} — {room.effect_text}",
    )


def dungeon_from_card(card) -> Dungeon | None:
    """Build a Dungeon from a card definition, using its Dungeon face."""
    for face in getattr(card, "faces", ()):
        type_line = getattr(face, "type_line", None)
        if type_line is None or "Dungeon" not in str(type_line):
            continue
        rooms, quality = parse_dungeon_text(face.oracle_text)
        return Dungeon(
            name=face.name,
            rooms=rooms,
            entry_quality=quality,
            face=face,
            oracle_id=getattr(card, "oracle_id", ""),
            abilities=tuple(room_ability(i, room) for i, room in enumerate(rooms)),
        )
    return None


def load_dungeons(db) -> tuple[Dungeon, ...]:
    """The rules' dungeons, read from a card database by name."""
    found = []
    for name in DUNGEON_NAMES:
        card = db.lookup(name)
        if card is None:
            continue
        dungeon = dungeon_from_card(card)
        if dungeon is not None:
            found.append(dungeon)
    return tuple(found)


_DEFAULT: tuple[Dungeon, ...] | None = None


def default_dungeons() -> tuple[Dungeon, ...]:
    """The dungeons from the local card database, read once per process."""
    global _DEFAULT
    if _DEFAULT is None:
        try:
            from ...data.db import CardDatabase

            with CardDatabase() as db:
                _DEFAULT = load_dungeons(db)
        except Exception:  # noqa: BLE001 - no database means no dungeons, not a crash
            _DEFAULT = ()
    return _DEFAULT


def available_dungeons(game: Game) -> tuple[Dungeon, ...]:
    """CR 309.2: the dungeon cards outside the game that players may use."""
    if not game.dungeons:
        game.dungeons = default_dungeons()
    return game.dungeons


# ---------------------------------------------------------------------------
# The dungeon in the command zone
# ---------------------------------------------------------------------------


def current_dungeon(game: Game, player_id: PlayerId) -> GameObject | None:
    """CR 309.3: the one dungeon card this player owns in the command zone."""
    player = game.player(player_id)
    obj = game.objects.get(player.dungeon)
    if obj is None or not obj.is_live or obj.zone is not Zone.COMMAND:
        return None
    if obj.id not in game.command:
        return None
    return obj


def definition_of(game: Game, obj: GameObject) -> Dungeon | None:
    return getattr(obj.card, "dungeon", None)


@dataclass(frozen=True, slots=True)
class _DungeonCard:
    """The card object behind a dungeon in the command zone.

    Shaped like the synthetic cards tokens and emblems use: the engine reads
    a face and, because ``abilities`` is set, takes the room abilities from
    here rather than asking the ability provider to parse dungeon text it was
    never written for.
    """

    name: str
    faces: tuple
    abilities: tuple[Ability, ...]
    dungeon: Dungeon
    oracle_id: str = ""
    mana_value: int = 0
    color_identity: Color = Color.NONE
    commander_legal: bool = False

    def __str__(self) -> str:
        return self.name


def _choose_dungeon(
    game: Game, player_id: PlayerId, options: list[Dungeon]
) -> Dungeon:
    """CR 309.2a: the player chooses. With no agent to ask, the first of the
    rules' own order, so a replay chooses the same way."""
    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_dungeon", None)
    if chooser is not None:
        names = [dungeon.name for dungeon in options]
        picked = chooser(game, player_id, names)
        if isinstance(picked, int) and 0 <= picked < len(options):
            return options[picked]
        for dungeon in options:
            if dungeon.name == picked:
                return dungeon
    return options[0]


def _choose_room(
    game: Game, player_id: PlayerId, dungeon: Dungeon, exits: tuple[int, ...]
) -> int:
    """CR 309.5a, 701.49b: at a fork the player chooses which arrow to follow.
    With no agent to ask, the first printed."""
    if len(exits) == 1:
        return exits[0]
    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_dungeon_room", None)
    if chooser is not None:
        names = [dungeon.rooms[index].name for index in exits]
        picked = chooser(game, player_id, names)
        if isinstance(picked, int) and 0 <= picked < len(exits):
            return exits[picked]
        for index in exits:
            if dungeon.rooms[index].name == picked:
                return index
    return exits[0]


def _move_marker(game: Game, player_id: PlayerId, obj: GameObject, room: int) -> None:
    """Put the venture marker on a room, which triggers its room ability."""
    player = game.player(player_id)
    player.venture_room = room
    dungeon = definition_of(game, obj)
    name = dungeon.rooms[room].name if dungeon is not None else f"room {room + 1}"
    game.log.record(
        game, f"{player.name} moves their venture marker to {name}", kind="dungeon",
        player=player_id,
    )
    # CR 309.4c: every room ability shares this one trigger condition.
    game.emit(
        Event(
            EventKind.VENTURE_MARKER_MOVED,
            object_id=obj.id,
            player=player_id,
            amount=room + 1,
        )
    )


def _enter_dungeon(game: Game, player_id: PlayerId, quality: str) -> GameObject | None:
    """CR 309.2a, 309.4a: bring a dungeon into the command zone and put the
    venture marker on its topmost room."""
    options = [
        dungeon
        for dungeon in available_dungeons(game)
        if dungeon.is_mappable
        and (
            dungeon.has_quality(quality) if quality else not dungeon.entry_quality
        )
    ]
    player = game.player(player_id)
    if not options:
        game.log.record(
            game, f"{player.name} has no dungeon to venture into", kind="unimplemented",
            player=player_id,
        )
        return None
    dungeon = _choose_dungeon(game, player_id, options)
    card = _DungeonCard(
        name=dungeon.name,
        faces=(dungeon.face,),
        abilities=dungeon.abilities,
        dungeon=dungeon,
        oracle_id=dungeon.oracle_id,
    )
    obj = game.create_object(card, player_id, Zone.COMMAND)
    player.dungeon = obj.id
    game.invalidate_characteristics()
    game.log.record(
        game, f"{player.name} ventures into {dungeon.name}", kind="dungeon", player=player_id
    )
    _move_marker(game, player_id, obj, 0)
    return obj


def complete_dungeon(game: Game, obj: GameObject) -> None:
    """CR 309.7: remove the dungeon from the game, and its owner completes it.

    The object is kept, superseded, rather than dropped: a room ability of it
    may still be waiting to resolve (CR 701.49c removes a dungeon whose last
    room's ability is still on the stack) and reads its source's last known
    information.
    """
    player = game.player(obj.owner)
    dungeon = definition_of(game, obj)
    name = dungeon.name if dungeon is not None else str(obj.card)
    game._remove_from_zone(obj)
    obj.superseded_by = obj.id
    if player.dungeon == obj.id:
        player.dungeon = NO_OBJECT
        player.venture_room = 0
    player.completed_dungeons.append(name)
    game.invalidate_characteristics()
    game.log.record(game, f"{player.name} completes {name}", kind="dungeon", player=player.id)
    game.emit(
        Event(EventKind.DUNGEON_COMPLETED, object_id=obj.id, player=player.id, data=(name,))
    )


def venture(game: Game, player_id: PlayerId, quality: str = "") -> None:
    """CR 701.49: venture into the dungeon, or into [quality] (701.49d)."""
    player = game.player(player_id)
    if player.has_lost:
        return
    obj = current_dungeon(game, player_id)
    dungeon = definition_of(game, obj) if obj is not None else None
    if obj is not None and dungeon is not None:
        exits = dungeon.rooms[player.venture_room].leads_to
        if exits:
            # CR 701.49b: follow an arrow to the next room.
            _move_marker(game, player_id, obj, _choose_room(game, player_id, dungeon, exits))
            game.emit(Event(EventKind.DUNGEON_VENTURED, player=player_id, amount=1))
            return
        # CR 701.49c: from the bottommost room, complete it and start another.
        complete_dungeon(game, obj)
    # CR 701.49a (and 701.49c's second half): enter a new dungeon.
    if _enter_dungeon(game, player_id, quality) is not None:
        game.emit(Event(EventKind.DUNGEON_VENTURED, player=player_id, amount=1))


def finished_dungeons(game: Game) -> list[GameObject]:
    """CR 704.5t: dungeons whose owner's marker is on the bottommost room and
    that are not the source of a room ability still waiting or on the stack.
    """
    from ..cr700_additional_rules.cr704_sba import _is_source_on_the_stack

    done = []
    for player in game.players:
        obj = current_dungeon(game, player.id)
        if obj is None:
            continue
        dungeon = definition_of(game, obj)
        if dungeon is None or player.venture_room != dungeon.bottommost:
            continue
        if _is_source_on_the_stack(game, obj):
            continue
        done.append(obj)
    return done


__all__ = [
    "DUNGEON_NAMES",
    "Dungeon",
    "Room",
    "available_dungeons",
    "complete_dungeon",
    "current_dungeon",
    "default_dungeons",
    "dungeon_from_card",
    "finished_dungeons",
    "load_dungeons",
    "parse_dungeon_text",
    "parse_room_effect",
    "room_ability",
    "venture",
]
