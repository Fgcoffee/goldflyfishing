"""Looking a few steps ahead: what an action would actually do, before doing it.

A bot that picks a spell by its card alone does not know that casting it
triggers something else it controls, or that it would do nothing at all. This
plays the action out on a private copy of the game - cast, resolve, state-based
actions, the triggers it sets off and their resolution - and reports what
changed. The real game is not touched.

Two uses. A bot can refuse an action the engine would refuse, or one that
leaves the board exactly as it was. And the ``Projection`` is a compact
before-and-after that a learned policy can read as features later.

What the bot may not know stays unknown. The copy's libraries are shuffled
before anything is drawn, so "draw a card" shows up as a card drawn, not as
which card; opponents are assumed to pass rather than respond, which is the
only assumption a bot without their hands can make.

The copy's randomness is its own. Nothing the lookahead does advances the real
game's random state or its log, so a replay of a seed is unchanged by whether
a bot looked ahead - which ``tests/sim`` depends on.
"""

from __future__ import annotations

import io
import pickle
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..rules.cr100_game_concepts.cr117_priority import Action
    from ..rules.kernel.game import Game
    from ..rules.kernel.ids import PlayerId

#: Stack objects the projection will resolve before giving up. A chain of
#: triggers is a handful; this only stops a loop from running forever.
MAX_RESOLUTIONS = 24


@dataclass(frozen=True, slots=True)
class PlayerDelta:
    """How one player's resources changed."""

    life: int = 0
    cards_drawn: int = 0
    hand: int = 0
    library: int = 0
    graveyard: int = 0
    poison: int = 0


@dataclass(slots=True)
class Projection:
    """What an action would do, measured on a copy of the game."""

    #: The engine accepted the action. False means it was illegal as offered -
    #: an unpayable cost, no legal target - and nothing else here is filled in.
    legal: bool
    #: Something other than paying for the action changed. A spell that
    #: resolves to nothing, or an ability that fizzles, is legal but pointless.
    changed: bool = False
    players: dict[int, PlayerDelta] = field(default_factory=dict)
    #: Names of permanents that arrived or left, by controller.
    entered: dict[int, list[str]] = field(default_factory=dict)
    left: dict[int, list[str]] = field(default_factory=dict)
    #: Stack objects resolved while projecting: the action itself plus every
    #: trigger it set off. More than one means it did something indirectly.
    resolutions: int = 0
    #: The stack was not empty when the projection stopped - a loop, or a
    #: chain longer than ``MAX_RESOLUTIONS``.
    unfinished: bool = False

    def features(self, player_id: int) -> tuple[float, ...]:
        """A fixed-length summary from ``player_id``'s point of view.

        Own deltas first, then the sum over opponents, then the board and the
        size of the chain. Stable in length and order, so it can feed a model.
        """
        own = self.players.get(player_id, PlayerDelta())
        others = [d for p, d in self.players.items() if p != player_id]

        def total(attr: str) -> int:
            return sum(getattr(d, attr) for d in others)

        return (
            float(self.legal),
            float(self.changed),
            float(own.life),
            float(own.cards_drawn),
            float(own.hand),
            float(own.library),
            float(own.graveyard),
            float(total("life")),
            float(total("cards_drawn")),
            float(total("hand")),
            float(total("poison")),
            float(len(self.entered.get(player_id, ()))),
            float(len(self.left.get(player_id, ()))),
            float(sum(len(v) for p, v in self.entered.items() if p != player_id)),
            float(sum(len(v) for p, v in self.left.items() if p != player_id)),
            float(self.resolutions),
            float(self.unfinished),
        )


def project(game: Game, player_id: PlayerId, action: Action) -> Projection:
    """Play ``action`` out on a copy of the game and report what it did."""
    from ..rules.cr100_game_concepts.cr117_priority import _perform, settle
    from ..rules.cr600_spells_and_abilities.cr608_stack import resolve_top

    source = game.objects.get(action.source)
    acting = game.characteristics(source).name if source is not None else ""
    before = _board(game, acting)
    shadow = clone(game, player_id)
    depth = len(shadow.stack)
    if not _perform(shadow, player_id, action):
        return Projection(legal=False)

    resolutions = 0
    settle(shadow)
    # Resolve what the action put on the stack and everything that triggers
    # from it, but nothing that was already waiting underneath: that was going
    # to resolve whatever this bot did.
    while len(shadow.stack) > depth and not shadow.game_over:
        if resolutions >= MAX_RESOLUTIONS:
            break
        resolve_top(shadow)
        resolutions += 1
        settle(shadow)

    after = _board(shadow, acting)
    projection = Projection(
        legal=True,
        resolutions=resolutions,
        unfinished=len(shadow.stack) > depth and not shadow.game_over,
    )
    _diff(before, after, projection)
    return projection


def clone(game: Game, player_id: PlayerId) -> Game:
    """A private copy of ``game`` in which ``player_id`` knows only what they may.

    Shared with the original: the card-ability provider, which holds a
    database connection, and every piece of frozen card data (see
    ``_immutable_types``). Replaced: the log (its running hash is the replay's
    determinism check) and the statistics observer. Everything else is copied,
    including the agents, so a decision made inside the projection leaves the
    real bots' memories alone.

    A pickle round trip rather than ``copy.deepcopy``: the same object graph,
    walked in C, at about half the cost - and a copy is made for every action
    a bot looks at.
    """
    from ..rules.kernel.log import GameLog

    frozen = _immutable_types()
    replaced = {id(game.log): GameLog(), id(game.observer): None}
    if game.ability_provider is not None:
        replaced[id(game.ability_provider)] = game.ability_provider
    shared: list[object] = []

    class _Pickler(pickle.Pickler):
        def persistent_id(self, obj):
            if type(obj) in frozen:
                shared.append(obj)
            elif id(obj) in replaced:
                shared.append(replaced[id(obj)])
            else:
                return None
            return len(shared) - 1

    class _Unpickler(pickle.Unpickler):
        def persistent_load(self, pid):
            return shared[pid]

    buffer = io.BytesIO()
    _Pickler(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(game)
    buffer.seek(0)
    shadow = _Unpickler(buffer).load()
    shadow.observer = None
    _hide_libraries(shadow, game)
    return shadow


_IMMUTABLE: frozenset[type] = frozenset()


def _immutable_types() -> frozenset[type]:
    """Card data the copy shares instead of copying.

    Printed and derived characteristics, abilities, effects, costs, filters and
    deck entries are frozen dataclasses: nothing changes one in place, a change
    makes a new one. Copying them was most of what a copy cost, for no
    difference at all.
    """
    global _IMMUTABLE
    if not _IMMUTABLE:
        from ..data.cards import CardDef, FaceDef
        from ..data.decks.model import Deck, DeckEntry, DeckIssue
        from ..rules.cr100_game_concepts.cr106_mana import ManaCost, ManaSymbol
        from ..rules.cr100_game_concepts.cr118_costs import Cost, CostComponent
        from ..rules.cr200_parts_of_a_card.characteristics import Characteristics
        from ..rules.cr200_parts_of_a_card.cr205_typeline import TypeLine
        from ..rules.cr500_turn_structure.restrictions import Restriction
        from ..rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
        from ..rules.cr600_spells_and_abilities.effects import Effect
        from ..rules.kernel.query import ObjectFilter, PlayerFilter, Value

        _IMMUTABLE = frozenset(
            {
                CardDef, FaceDef, Deck, DeckEntry, DeckIssue, ManaCost, ManaSymbol,
                Cost, CostComponent, Characteristics, TypeLine, Restriction, Ability,
                TriggerCondition, Effect, ObjectFilter, PlayerFilter, Value,
            }
        )
    return _IMMUTABLE


def _hide_libraries(shadow: Game, game: Game) -> None:
    """Shuffle every library, so a projected draw is a card, not *the* card.

    Seeded from the game's own position rather than from its random state:
    drawing from that would advance it, and the real game would diverge from
    its replay depending on whether a bot looked ahead.
    """
    # Not ``hash()``: string hashing is salted per process, and two workers
    # replaying one seed must shuffle alike.
    rng = random.Random(f"{game.turn}|{int(game.step)}|{game.log.digest()}")
    for player in shadow.players:
        library = list(player.library)
        rng.shuffle(library)
        player.library[:] = library


# ---------------------------------------------------------------------------
# Before and after
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    #: (life, hand size, library size, graveyard size, poison, drawn this turn).
    players: dict[int, tuple[int, int, int, int, int, int]]
    #: (controller, name, power, toughness, counters, attached, types) per permanent.
    permanents: dict[int, tuple]
    #: Every card by (zone, owner, name), counted: hands, graveyards, exile.
    cards: Counter
    #: The name of the card the action is about, so its own move can be excused.
    acting: str


def _board(game: Game, acting: str = "") -> _Snapshot:
    """Everything a spell could plausibly change, and nothing about paying for it.

    Tapped status and mana pools are left out on purpose: they are what the
    cost changes, and a spell that only taps lands to do nothing did nothing.
    """
    players = {
        p.id: (
            p.life,
            len(p.hand),
            len(p.library),
            len(p.graveyard),
            p.poison,
            p.cards_drawn_this_turn,
        )
        for p in game.players
    }
    permanents = {}
    for obj in game.permanents():
        chars = game.characteristics(obj)
        permanents[obj.id] = (
            obj.controller,
            chars.name,
            chars.power,
            chars.toughness,
            tuple(sorted((str(k), v) for k, v in (obj.counters or {}).items())),
            obj.attached_to,
            int(chars.type_line.types),
        )
    cards: Counter = Counter()
    for p in game.players:
        for zone, ids in (("hand", p.hand), ("graveyard", p.graveyard)):
            for object_id in ids:
                cards[(zone, p.id, _name(game, object_id))] += 1
    for object_id in game.exile:
        obj = game.objects.get(object_id)
        if obj is not None:
            cards[("exile", obj.owner, _name(game, object_id))] += 1
    return _Snapshot(players, permanents, cards, acting)


def _name(game: Game, object_id) -> str:
    obj = game.objects.get(object_id)
    return game.characteristics(obj).name if obj is not None else ""


def _diff(before: _Snapshot, after: _Snapshot, projection: Projection) -> None:
    for pid, (life, hand, library, graveyard, poison, drawn) in before.players.items():
        a_life, a_hand, a_library, a_graveyard, a_poison, a_drawn = after.players[pid]
        projection.players[pid] = PlayerDelta(
            life=a_life - life,
            cards_drawn=a_drawn - drawn,
            hand=a_hand - hand,
            library=a_library - library,
            graveyard=a_graveyard - graveyard,
            poison=a_poison - poison,
        )

    for oid, row in after.permanents.items():
        if oid not in before.permanents:
            projection.entered.setdefault(row[0], []).append(row[1])
    for oid, row in before.permanents.items():
        if oid not in after.permanents:
            projection.left.setdefault(row[0], []).append(row[1])

    # An unfinished chain did something, even if the part that was seen did
    # not: calling it pointless would throw away a combo mid-way.
    projection.changed = projection.unfinished or _meaningful(before, after)


def _meaningful(before: _Snapshot, after: _Snapshot) -> bool:
    """Whether anything changed beyond the action paying for itself.

    The acting card leaving a hand, and landing in a graveyard or exile, is the
    spell being cast and resolving - not the spell doing something. Anything
    else moving is a real change: a card returned to hand, a card drawn, a card
    exiled. Compared by name rather than by count, because Kolaghan's Command
    returning a creature card nets a hand and a graveyard out to the same sizes.
    """
    if before.permanents != after.permanents:
        return True
    for pid, row in before.players.items():
        a = after.players[pid]
        # Life, library, poison, draws. Hand and graveyard are judged by name.
        if (row[0], row[2], row[4], row[5]) != (a[0], a[2], a[4], a[5]):
            return True
    gone = before.cards - after.cards
    came = after.cards - before.cards
    for moved in (gone, came):
        for key in [k for k in moved if k[2] == before.acting]:
            moved[key] -= 1
            if moved[key] <= 0:
                del moved[key]
            break
    return bool(+gone or +came)
