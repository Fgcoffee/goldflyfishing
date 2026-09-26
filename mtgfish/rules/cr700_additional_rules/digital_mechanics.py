"""MTG Arena's digital-only mechanics: perpetually, conjure and seek.

None of these is in the Comprehensive Rules. They exist only on MTG Arena and
on the cards legal in its formats (Historic, Alchemy, Timeless, Brawl), and
their rules are what Arena publishes for them - which is what each function
here cites instead of a CR number. The CR still governs everything around
them: a conjured card is an ordinary card once it exists, a sought card moves
from library to hand like any other, and a perpetual change is applied by the
layer system like any other continuous effect.

**Seek.** The reminder text Arena prints on its own cards: "To seek a card,
put one at random from your library into your hand." A seek names what kind
of card ("seek a land card"), picks at random among the cards in the library
that are that kind, and does nothing if there are none. It is not a search -
the library is not shuffled and nothing is revealed - and it is not a draw.

**Conjure.** Arena: a conjured card is created from outside the game - it was
never in the player's deck or sideboard - in the zone the effect names, and
behaves as a normal card from then on. The player who conjures it owns it.
The card is named by the text ("conjure a card named Lightning Bolt"); the
engine finds that card through ``Game.card_catalog``, which is the card
database in a real game. The parser supplies only the name it read.

A *duplicate* is a conjured card that is the same card as an existing one -
and Arena's duplicates keep the perpetual changes of the card they copy
("conjure a duplicate ... then both of them perpetually lose double team"
only makes sense because the two are then changed alike).

**Perpetually.** Arena: an effect that perpetually changes a card applies to
that card for the rest of the game, whatever zone it moves to - graveyard,
exile, hand, or shuffled back into the library. That is the one thing it adds
to an ordinary continuous effect, and it is the exact exception to CR 400.7
(a moved card is a new object with no memory of the old one). So a perpetual
change is recorded against the card (``Game.perpetual``), carried to the new
object at every zone change (``Game.move_object``), and handed to the layer
system as a continuous effect of that object with the timestamp it was
created at (CR 613.7). Numbers in it are fixed as it is created - "gets
+X/+X, where X is the life you gained" does not grow later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from ..cr600_spells_and_abilities.effects import Effect, EffectKind
from ..kernel.enums import Duration, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject, ObjectKind
from ..kernel.ids import NO_OBJECT, ObjectId, PlayerId
from ..kernel.query import Value

if TYPE_CHECKING:
    from ..kernel.game import ContinuousEffect, Game


class CardCatalog(Protocol):
    """What ``Game.card_catalog`` must offer: a card definition by name."""

    def lookup(self, name: str) -> object | None: ...


# ---------------------------------------------------------------------------
# Perpetually
# ---------------------------------------------------------------------------

#: The changes "perpetually" can make: the layered continuous effects. Each
#: one is applied to the card it is bound to and nothing else.
PERPETUAL_KINDS = frozenset(
    {
        EffectKind.MODIFY_PT,
        EffectKind.SET_PT,
        EffectKind.SWITCH_PT,
        EffectKind.GRANT_ABILITY,
        EffectKind.REMOVE_ABILITIES,
        EffectKind.SET_COLOR,
        EffectKind.ADD_TYPE,
        EffectKind.REMOVE_TYPE,
        EffectKind.SET_TYPE,
    }
)


@dataclass(frozen=True, slots=True)
class PerpetualChange:
    """One perpetual change bound to one card.

    ``effect`` is aimed at the card itself (``targets`` is ``None``) and holds
    only constant amounts. ``timestamp`` is when it was created, which is its
    place among the other effects of its layer (CR 613.7).
    """

    effect: Effect
    controller: PlayerId
    timestamp: int


def perpetually(
    game: Game,
    obj: GameObject,
    effect: Effect,
    controller: PlayerId,
    *,
    amounts: tuple[int, int] | None = None,
) -> PerpetualChange | None:
    """Bind a change to a card for the rest of the game.

    ``amounts`` are the change's two numbers, already worked out: they are
    fixed now, as the effect is created. A token has no card to bind the
    change to past the battlefield; it is bound all the same, and ceases to
    matter when the token ceases to exist (CR 111.7).
    """
    if effect.kind not in PERPETUAL_KINDS or not obj.is_live:
        return None
    changes = {"targets": None, "is_targeted": False, "duration": int(Duration.PERMANENT)}
    if amounts is not None:
        changes["amount"] = Value.of(amounts[0])
        changes["amount2"] = Value.of(amounts[1])
    change = PerpetualChange(
        effect=replace(effect, **changes),
        controller=controller,
        timestamp=game.ids.timestamp(),
    )
    game.perpetual[obj.id] = game.perpetual.get(obj.id, ()) + (change,)
    game.invalidate_characteristics()
    return change


def perpetual_changes(game: Game, obj: GameObject) -> tuple[PerpetualChange, ...]:
    return game.perpetual.get(obj.id, ())


def perpetually_changed(game: Game) -> list[GameObject]:
    """Every live card carrying a perpetual change, wherever it is.

    The layer system brings these into its computation even from a hand, a
    library or a graveyard, because a perpetual change applies in every zone.
    """
    out = []
    for object_id in game.perpetual:
        obj = game.objects.get(object_id)
        if obj is not None and obj.is_live:
            out.append(obj)
    return out


def perpetual_continuous_effects(
    game: Game, by_id: dict[ObjectId, GameObject]
) -> list[ContinuousEffect]:
    """The perpetual changes of the objects being computed, as layer inputs.

    Each is a continuous effect whose source is the card it changes, which is
    what ``targets=None`` means to the layer system: the effect applies to its
    own source and nothing else.
    """
    from ..cr600_spells_and_abilities.cr613_layers import layer_for
    from ..kernel.game import ContinuousEffect

    out: list[ContinuousEffect] = []
    for object_id, obj in by_id.items():
        for change in game.perpetual.get(object_id, ()):
            out.append(
                ContinuousEffect(
                    effect=change.effect,
                    source=obj.id,
                    controller=change.controller,
                    timestamp=change.timestamp,
                    layer=int(layer_for(change.effect)),
                    duration=int(Duration.PERMANENT),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Conjure
# ---------------------------------------------------------------------------


def conjure(
    game: Game,
    player_id: PlayerId,
    card: object,
    zone: Zone,
    *,
    tapped: bool = False,
    from_top: int = 0,
    source: ObjectId = NO_OBJECT,
) -> GameObject | None:
    """Create a card from outside the game in ``zone``, owned by the player.

    It arrives the way any card arrives there: the move is offered to
    replacement effects first (CR 614 - "if a card would be put into a
    graveyard from anywhere, exile it instead" applies to a conjured card
    too), a permanent entering applies its own "as this enters" effects and
    enters with its loyalty or defense (CR 306.5b, 310.4b), and the zone
    change is announced with no zone of origin, because it had none.

    ``from_top`` places it that far down a library ("seventh from the top");
    zero leaves it at the bottom, which is only ever asked for when the text
    shuffles the library straight after.
    """
    from ..cr600_spells_and_abilities.cr614_replacement import apply_replacements

    player = game.player(player_id)
    if player.has_lost:
        return None

    obj = GameObject(
        id=game.ids.object_id(),
        kind=ObjectKind.CARD,
        owner=player_id,
        controller=player_id,
        base_controller=player_id,
        zone=zone,
        card=card,
        timestamp=game.ids.timestamp(),
    )
    game.objects[obj.id] = obj

    prospective = Event(
        EventKind.ZONE_CHANGE,
        object_id=obj.id,
        player=player_id,
        source=source,
        to_zone=zone,
    )
    if game.has_replacements:
        replaced = apply_replacements(game, prospective)
        if replaced is None:
            game.objects.pop(obj.id, None)
            return None
        prospective = replaced
    zone = zone if prospective.to_zone is None else prospective.to_zone
    obj.zone = zone

    if zone is Zone.BATTLEFIELD:
        if not game._may_enter_the_battlefield(obj):
            # CR 304.4, 307.4: an instant or sorcery can't enter the
            # battlefield, and a card with nowhere to be is not created.
            game.objects.pop(obj.id, None)
            return None
        from ..cr300_card_types.cr300_characteristics import entering_counters

        for kind, amount in entering_counters(game.characteristics(obj)):
            obj.add_counters(kind, amount)
        obj.entered_battlefield_turn = game.turn
        obj.summoning_sick = True
        obj.tapped = tapped
        game.choose_protector(obj)

    target = game.zone_list(zone, player_id)
    if zone is Zone.LIBRARY and from_top > 0:
        target.insert(min(from_top - 1, len(target)), obj.id)
    else:
        target.append(obj.id)
    game.invalidate_characteristics()
    game.log.record(
        game,
        f"{player.name} conjures {getattr(card, 'name', card)} into {zone.name.lower()}",
        kind="conjure",
        player=player_id,
    )
    game.emit(
        Event(
            EventKind.ZONE_CHANGE,
            object_id=obj.id,
            player=player_id,
            source=source,
            to_zone=zone,
        )
    )
    game._emit_zone_specific(obj, None, zone)
    return obj


def conjure_duplicate(
    game: Game,
    player_id: PlayerId,
    original: GameObject,
    zone: Zone,
    *,
    keep_perpetual: bool,
    tapped: bool = False,
    source: ObjectId = NO_OBJECT,
) -> GameObject | None:
    """Conjure the same card as ``original``; a duplicate keeps its perpetual
    changes, a card merely named the same does not."""
    if original.card is None or original.kind is not ObjectKind.CARD:
        # A token or an ability has no card to be a duplicate of.
        return None
    made = conjure(game, player_id, original.card, zone, tapped=tapped, source=source)
    if made is not None and keep_perpetual:
        carried = game.perpetual.get(original.id, ())
        if carried:
            game.perpetual[made.id] = carried
            game.invalidate_characteristics()
    return made


def catalog_card(game: Game, name: str) -> object | None:
    """The card a conjure names, or ``None`` - logged - if it can't be found."""
    catalog = game.card_catalog
    card = catalog.lookup(name) if catalog is not None else None
    if card is None:
        game.log.record(
            game,
            f"cannot conjure {name!r}: "
            + ("no card catalogue for this game" if catalog is None else "no such card"),
            kind="unsupported",
        )
    return card


# ---------------------------------------------------------------------------
# Seek
# ---------------------------------------------------------------------------


def seek(
    game: Game,
    player_id: PlayerId,
    wanted,
    count: int,
    *,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId | None = None,
    to_zone: Zone = Zone.HAND,
) -> list[GameObject]:
    """Put ``count`` cards at random from among those in the player's library
    matching ``wanted`` into their hand; fewer if there are fewer.

    ``wanted`` is an ``ObjectFilter`` (``None`` for "a card"). The random pick
    draws from ``Game.rng`` so a replay picks the same cards. Returns the
    cards as they are in their new zone.
    """
    from ..kernel.matching import matches

    player = game.player(player_id)
    asker = player_id if controller is None else controller
    pool = []
    for object_id in player.library:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        if wanted is None or matches(game, obj, wanted, source=source, controller=asker):
            pool.append(obj)

    picked: list[GameObject] = []
    for _ in range(max(0, count)):
        if not pool:
            break
        obj = pool.pop(game.rng.randrange(len(pool)))
        picked.append(game.move_object(obj, to_zone, to_player=player_id))
    return picked


__all__ = [
    "PERPETUAL_KINDS",
    "CardCatalog",
    "PerpetualChange",
    "catalog_card",
    "conjure",
    "conjure_duplicate",
    "perpetual_changes",
    "perpetual_continuous_effects",
    "perpetually",
    "perpetually_changed",
    "seek",
]
