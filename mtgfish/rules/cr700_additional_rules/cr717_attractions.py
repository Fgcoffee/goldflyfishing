"""Attractions (CR 717), and the two actions that use them (CR 701.51, 701.52).

An Attraction is an artifact card that never starts in the library. CR 717.2
gives its owner a supplementary Attraction deck that exists in the command
zone; opening an Attraction moves that deck's top card onto the battlefield
(CR 701.51b), and rolling to visit rolls a six-sided die and visits every
Attraction its roller controls with that number lit up (CR 701.52a). Visiting
is what a visit ability triggers on (CR 702.159a, built in
``cr702_keyword_impl``).

Neither the Attraction deck nor the junkyard is a zone. The deck exists *in*
the command zone (CR 717.2), and CR 717.6a says outright that the junkyard is
not its own zone - so both are piles of command-zone objects, told apart by
``GameObject.command_pile``. The deck is ordered, top first, in the order the
command zone holds it.

Which numbers an Attraction has lit up is printed on the physical card
(CR 717.1), and two cards with the same name may differ. It is read from the
card definition's ``attraction_lights``. The card snapshot this engine ships
has none, so in a real game no Attraction is ever visited - the roll happens,
and nothing lights up.
"""

from __future__ import annotations

from typing import Iterable

from ..kernel.enums import Zone
from ..kernel.events import Event, EventKind
from ..kernel.game import Game
from ..kernel.gameobject import GameObject, ObjectKind
from ..kernel.ids import PlayerId

#: CR 717.2: the pile a player's face-down Attraction deck is kept in.
ATTRACTION_DECK = "attraction deck"
#: CR 717.6a: the face-up pile of that player's Attractions that went to the
#: command zone instead of somewhere else.
JUNKYARD = "junkyard"

#: CR 701.52a: the die rolled to visit is six-sided.
VISIT_DIE_SIDES = 6

#: CR 717.6: the zones an Astrotorium-backed card may go to as itself.
_ASTROTORIUM_ZONES = frozenset({Zone.BATTLEFIELD, Zone.EXILE, Zone.COMMAND})


# ---------------------------------------------------------------------------
# Cards and piles
# ---------------------------------------------------------------------------


def has_astrotorium_back(obj: GameObject) -> bool:
    """CR 717.1: whether this is an Attraction *card*, with its card back.

    A physical fact, so it is read from the printed card rather than from the
    object's current characteristics - an effect that makes something an
    Attraction does not change what is printed on the back of it. A token or
    a copy is not a card and has no back at all.
    """
    if obj.kind is not ObjectKind.CARD or obj.card is None:
        return False
    faces = getattr(obj.card, "faces", ())
    return bool(faces) and faces[0].type_line.has_subtype("Attraction")


def lit_numbers(obj: GameObject) -> frozenset[int]:
    """CR 717.1: the numbers lit up on this Attraction's card."""
    if obj.card is None:
        return frozenset()
    return frozenset(getattr(obj.card, "attraction_lights", ()) or ())


def _pile(game: Game, player_id: PlayerId, pile: str) -> list[GameObject]:
    out = []
    for object_id in game.command:
        obj = game.objects.get(object_id)
        if obj is not None and obj.owner == player_id and obj.command_pile == pile:
            out.append(obj)
    return out


def attraction_deck(game: Game, player_id: PlayerId) -> list[GameObject]:
    """CR 717.2: a player's Attraction deck, top card first."""
    return _pile(game, player_id, ATTRACTION_DECK)


def junkyard(game: Game, player_id: PlayerId) -> list[GameObject]:
    """CR 717.6a: a player's junkyard."""
    return _pile(game, player_id, JUNKYARD)


def set_up_attraction_deck(game: Game, player_id: PlayerId, cards: Iterable) -> None:
    """CR 717.2: the Attraction deck begins the game in the command zone."""
    for card in cards:
        obj = game.create_object(card, player_id, Zone.COMMAND)
        obj.command_pile = ATTRACTION_DECK


def shuffle_attraction_deck(game: Game, player_id: PlayerId) -> None:
    """CR 103.3a: a supplementary deck is shuffled by its owner.

    The deck's cards are taken out of the command zone's list and put back in
    random order; where they sit relative to a commander or an emblem means
    nothing, since only their order among themselves is the deck's.
    """
    ids = [obj.id for obj in attraction_deck(game, player_id)]
    if not ids:
        return
    for object_id in ids:
        game.command.remove(object_id)
    game.rng.shuffle(ids)
    game.command.extend(ids)


def controls_an_attraction(game: Game, player_id: PlayerId) -> bool:
    """Whether a player controls one or more Attractions (CR 505.5)."""
    return any(
        game.characteristics(obj).has_subtype("Attraction")
        for obj in game.permanents(player_id)
    )


# ---------------------------------------------------------------------------
# CR 717.6: the junkyard replacement
# ---------------------------------------------------------------------------


def junkyard_replacement(game: Game, obj: GameObject, event: Event) -> Event:
    """CR 717.6: an Astrotorium-backed card bound for anywhere but the
    battlefield, exile or the command zone goes to the command zone instead.

    Applied after every other replacement has had its turn, and whatever they
    produced: CR 717.6 may apply more than once to one event, so a card that
    another effect redirects to a graveyard or library is caught again.
    """
    destination = event.to_zone
    if destination is None or destination in _ASTROTORIUM_ZONES:
        return event
    if not has_astrotorium_back(obj):
        return event
    game.log.record(
        game,
        f"{obj} goes to its owner's junkyard instead of {destination.name.lower()}",
        kind="attraction",
        player=obj.owner,
    )
    return event.replaced(to_zone=Zone.COMMAND)


def arrived_in_command_zone(obj: GameObject) -> None:
    """CR 717.6a: an Astrotorium-backed card put into the command zone this
    way joins its owner's junkyard.

    The Attraction deck is only ever built at setup, so a card of this kind
    arriving in the command zone by a move is always arriving by CR 717.6.
    """
    if has_astrotorium_back(obj):
        obj.command_pile = JUNKYARD


# ---------------------------------------------------------------------------
# CR 701.51: open an Attraction
# ---------------------------------------------------------------------------


def open_attraction(game: Game, player_id: PlayerId) -> GameObject | None:
    """CR 701.51b: the top card of your Attraction deck, face up, onto the
    battlefield under your control.

    Returns the Attraction on the battlefield, or None when there was nothing
    to open (CR 701.51a covers a player with no deck; an empty one is the
    same impossibility) or it never arrived.
    """
    deck = attraction_deck(game, player_id)
    if not deck:
        return None
    top = deck[0]
    opened = game.move_object(top, Zone.BATTLEFIELD, to_player=player_id)
    # CR 701.51c: only an Attraction that actually reached the battlefield
    # was opened. A move that was prevented hands back the old object, and a
    # replaced one lands somewhere else.
    if opened is top or opened.zone is not Zone.BATTLEFIELD:
        return None
    game.log.record(
        game,
        f"{game.player(player_id).name} opens {opened}",
        kind="attraction",
        player=player_id,
    )
    game.emit(
        Event(EventKind.ATTRACTION_OPENED, object_id=opened.id, player=player_id)
    )
    return opened


# ---------------------------------------------------------------------------
# CR 701.52: roll to visit your Attractions
# ---------------------------------------------------------------------------


def roll_to_visit(game: Game, player_id: PlayerId) -> int:
    """CR 701.52a: roll a six-sided die, then visit each Attraction you
    control with that number lit up.

    Each visit is its own event, and a visit ability triggers on its own
    Attraction's (CR 702.159a). Returns the result.
    """
    from ..cr100_game_concepts.actions import roll_die

    result = roll_die(game, player_id, VISIT_DIE_SIDES)
    game.emit(Event(EventKind.ROLLED_TO_VISIT, player=player_id, amount=result))
    visited = [
        obj
        for obj in game.permanents(player_id)
        if game.characteristics(obj).has_subtype("Attraction")
        and result in lit_numbers(obj)
    ]
    for obj in visited:
        game.log.record(
            game,
            f"{game.player(player_id).name} visits {obj}",
            kind="attraction",
            player=player_id,
        )
        game.emit(
            Event(
                EventKind.ATTRACTION_VISITED,
                object_id=obj.id,
                player=player_id,
                amount=result,
            )
        )
    return result
