"""The board, as a person would look at it, sampled through a replay.

The log says what happened. It does not say what the table *looked like*, and
after fifty turns a log is a very long way of saying "your deck made five
thousand Ape tokens". Anyone who plays Magic reads a board in a second and a
log in ten minutes, so a replay that can only be read is a replay that mostly
is not.

So a replay is also a film: the whole visible position, sampled at every moment
that could have changed it, each sample tagged with the log entry it is current
as of. The viewer scrubs one and the other follows.

Three things make this affordable, because the naive version is 12 MB of JSON
for one game:

**Identity is stored once.** A permanent in a snapshot is an integer into
``BoardFilm.cards``; the name, art and parse verdict live there. Repeating them
for fifty permanents across six hundred snapshots is most of that 12 MB.

**Identical boards collapse.** Sampling on every interesting event gives about
3,700 samples of which roughly 600 differ - the rest are a step beginning and
ending with nothing between. A sample equal to the one before it extends that
one's reach instead of being stored, which loses nothing: the board really was
unchanged.

**Nothing is computed that the engine would not compute anyway.** Power and
toughness come from ``characteristics``, which is memoised per epoch, so a
watched game plays the same game at close to the same speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ..rules.enums import Zone
from ..rules.events import EventKind
from ..rules.gameobject import ObjectKind

if TYPE_CHECKING:
    from ..rules.game import Game

#: Events after which the board may look different. Generous on purpose: an
#: event that changed nothing costs one comparison and is then dropped, while
#: an event left out is a change the viewer never shows.
SAMPLE_AFTER: frozenset[EventKind] = frozenset(
    {
        EventKind.ZONE_CHANGE,
        EventKind.ENTERS_BATTLEFIELD,
        EventKind.LEAVES_BATTLEFIELD,
        EventKind.DIES,
        EventKind.TOKEN_CREATED,
        EventKind.CEASED_TO_EXIST,
        EventKind.CAST_SPELL,
        EventKind.SPELL_RESOLVED,
        EventKind.ABILITY_RESOLVED,
        EventKind.PUT_ON_STACK,
        EventKind.COUNTERED,
        EventKind.FIZZLED,
        EventKind.TAPPED,
        EventKind.UNTAPPED,
        EventKind.COUNTER_ADDED,
        EventKind.COUNTER_REMOVED,
        EventKind.ATTACHED,
        EventKind.UNATTACHED,
        EventKind.TRANSFORMED,
        EventKind.PHASED_OUT,
        EventKind.PHASED_IN,
        EventKind.CONTROL_CHANGED,
        EventKind.DAMAGE_DEALT,
        EventKind.COMBAT_DAMAGE_DEALT,
        EventKind.LIFE_GAINED,
        EventKind.LIFE_LOST,
        EventKind.LIFE_SET,
        EventKind.POISON_ADDED,
        EventKind.DREW_CARD,
        EventKind.DISCARDED,
        EventKind.MILLED,
        EventKind.ATTACKERS_DECLARED,
        EventKind.BLOCKERS_DECLARED,
        EventKind.REMOVED_FROM_COMBAT,
        EventKind.TURN_BEGAN,
        EventKind.STEP_BEGAN,
        EventKind.STEP_ENDED,
        EventKind.PLAYER_LOST,
    }
)


@dataclass(frozen=True, slots=True)
class BoardCardRef:
    """A distinct printed card - or token face - the viewer has to draw.

    ``oracle_id`` is empty for a token, which has no printing and therefore no
    art to fetch; the viewer draws those from the name and the type line.
    """

    name: str
    oracle_id: str = ""
    type_line: str = ""
    mana_cost: str = ""
    token: bool = False


@dataclass(slots=True)
class BoardCard:
    """One permanent, with everything needed to draw it and nothing else."""

    id: int
    card: int
    tapped: bool = False
    power: int | None = None
    toughness: int | None = None
    damage: int = 0
    counters: dict = field(default_factory=dict)
    sick: bool = False
    #: The player this creature is attacking, or -1. A separate field from
    #: ``attacking_permanent`` because player ids and object ids are both small
    #: integers - see ``rules.combat.Combat``.
    attacking: int = -1
    attacking_permanent: int = -1
    blocking: tuple[int, ...] = ()
    token: bool = False
    commander: bool = False
    face_down: bool = False
    phased_out: bool = False
    attached_to: int = -1
    #: True when no ability of this card was read. The whole reason to draw a
    #: board rather than describe it: a permanent that does nothing is visible
    #: as a blank the moment it lands, not inferred from a log that never
    #: mentions it again.
    unreadable: bool = False


@dataclass(slots=True)
class BoardSeat:
    """One player, and what they have."""

    player: int
    life: int
    hand: int
    library: int
    graveyard: int
    exile: int
    poison: int = 0
    out: bool = False
    permanents: list[BoardCard] = field(default_factory=list)
    #: The cards in hand and in the graveyard, when the viewer is allowed to
    #: see them. Empty in a replay, always: a replay that listed a hand would
    #: be showing information nobody at the table had. The sandbox is not a
    #: game - it is a bench with one operator - and shows everything.
    hand_cards: list[BoardCard] = field(default_factory=list)
    graveyard_cards: list[BoardCard] = field(default_factory=list)


@dataclass(slots=True)
class BoardStackItem:
    card: int
    controller: int
    #: Spell, or the text of the ability waiting to resolve.
    text: str = ""


@dataclass(slots=True)
class BoardSnapshot:
    """The whole visible position at one moment."""

    #: The log entry this board is current as of. The viewer scrubs the log and
    #: shows the last snapshot at or before wherever it is.
    frame: int
    turn: int
    phase: str
    step: str
    active: int
    seats: list[BoardSeat] = field(default_factory=list)
    #: Top of the stack last, the way it resolves.
    stack: list[BoardStackItem] = field(default_factory=list)

    def same_board_as(self, other: BoardSnapshot) -> bool:
        """Whether nothing a viewer could see has changed.

        ``frame`` is excluded on purpose - that is the whole point - and so are
        turn, phase and step, which are shown from the log's own heading.
        """
        return self.seats == other.seats and self.stack == other.stack


@dataclass(slots=True)
class BoardFilm:
    """Every distinct card in the game, and the board over time."""

    cards: list[BoardCardRef] = field(default_factory=list)
    snapshots: list[BoardSnapshot] = field(default_factory=list)

    def at_frame(self, frame: int) -> BoardSnapshot | None:
        """The board as it stood at a log entry - the last one at or before it."""
        found = None
        for snapshot in self.snapshots:
            if snapshot.frame > frame:
                break
            found = snapshot
        return found


class BoardRecorder:
    """Samples the board, attached to a game as a second observer.

    Deliberately read-only: it asks the engine only for things the engine has
    already computed, and never asks it to do anything. A watched game has to
    be the same game as an unwatched one, or the film shows a board the
    statistics did not come from.
    """

    __slots__ = ("film", "open_zones", "_index", "_previous")

    def __init__(self, *, open_zones: bool = False) -> None:
        self.film = BoardFilm()
        #: Whether to record the contents of hands and graveyards rather than
        #: only their size. False for a replay, and the default is False so
        #: that leaking a hand has to be asked for.
        self.open_zones = open_zones
        #: card reference -> its index in ``film.cards``.
        self._index: dict[BoardCardRef, int] = {}
        self._previous: BoardSnapshot | None = None

    def __call__(self, game: Game, event) -> None:
        if event.kind not in SAMPLE_AFTER:
            return
        self.sample(game)

    def sample(self, game: Game) -> None:
        """Take one snapshot, unless it would repeat the last one."""
        snapshot = self._snapshot(game)
        previous = self._previous
        if previous is not None and snapshot.same_board_as(previous):
            # The board really is unchanged, so the earlier snapshot simply
            # stays current for longer. Storing it again would be storing the
            # same picture twice.
            return
        self.film.snapshots.append(snapshot)
        self._previous = snapshot

    # -- building a snapshot ------------------------------------------------

    def _snapshot(self, game: Game) -> BoardSnapshot:
        combat = game.combat
        seats = [self._seat(game, player, combat) for player in game.players]
        return BoardSnapshot(
            frame=len(game.log.entries),
            turn=game.turn,
            phase=game.phase.name,
            step=game.step.name,
            active=int(game.active_player),
            seats=seats,
            stack=[
                item
                for object_id in game.stack
                if (item := self._stack_item(game, object_id)) is not None
            ],
        )

    def _hidden(self, game: Game, object_ids) -> list[BoardCard]:
        """Cards in a zone a viewer is allowed to look into."""
        if not self.open_zones:
            return []
        out = []
        for object_id in object_ids:
            obj = game.objects.get(object_id)
            if obj is not None:
                out.append(self._permanent(game, obj, None))
        return out

    def _seat(self, game: Game, player, combat) -> BoardSeat:
        return BoardSeat(
            player=int(player.id),
            life=player.life,
            hand=len(player.hand),
            library=len(player.library),
            graveyard=len(player.graveyard),
            exile=sum(
                1
                for object_id in game.exile
                if (obj := game.objects.get(object_id)) is not None
                and obj.owner == player.id
            ),
            poison=player.poison,
            out=player.has_lost,
            permanents=[
                self._permanent(game, obj, combat)
                for obj in game.permanents(player.id)
            ],
            hand_cards=self._hidden(game, player.hand),
            graveyard_cards=self._hidden(game, player.graveyard),
        )

    def _permanent(self, game: Game, obj, combat) -> BoardCard:
        chars = game.characteristics(obj)
        attacking = -1
        attacking_permanent = -1
        blocking: tuple[int, ...] = ()
        if combat is not None:
            if obj.id in combat.attacking:
                attacking = int(combat.attacking[obj.id])
                attacking_permanent = int(
                    combat.attacking_permanent.get(obj.id, -1)
                )
            blocked = combat.blocking.get(obj.id)
            if blocked:
                blocking = tuple(int(i) for i in blocked)
        return BoardCard(
            id=int(obj.id),
            card=self._card_index(obj, chars),
            tapped=obj.tapped,
            power=chars.power,
            toughness=chars.toughness,
            damage=obj.damage,
            counters={k: v for k, v in obj.counters.items() if v},
            sick=obj.summoning_sick and chars.is_creature,
            attacking=attacking,
            attacking_permanent=attacking_permanent,
            blocking=blocking,
            token=obj.kind is ObjectKind.TOKEN,
            commander=obj.is_commander,
            face_down=obj.face_down,
            phased_out=obj.phased_out,
            attached_to=int(obj.attached_to) if obj.attached_to else -1,
            unreadable=bool(chars.abilities)
            and all(a.unparsed for a in chars.abilities),
        )

    def _stack_item(self, game: Game, object_id) -> BoardStackItem | None:
        obj = game.objects.get(object_id)
        if obj is None:
            return None
        chars = game.characteristics(obj)
        return BoardStackItem(
            card=self._card_index(obj, chars),
            controller=int(obj.controller),
            text=(obj.ability.text if obj.ability is not None else "") or "",
        )

    def _card_index(self, obj, chars) -> int:
        """Intern this card, so a snapshot stores an integer and not a name."""
        card = getattr(obj, "card", None)
        ref = BoardCardRef(
            name=chars.name or getattr(card, "name", "") or "",
            oracle_id=getattr(card, "oracle_id", "") or "",
            type_line=str(chars.type_line),
            mana_cost=str(chars.mana_cost) if chars.mana_cost else "",
            token=obj.kind is ObjectKind.TOKEN,
        )
        index = self._index.get(ref)
        if index is None:
            index = len(self.film.cards)
            self._index[ref] = index
            self.film.cards.append(ref)
        return index
