"""Face-down spells and permanents (CR 708), and the actions that make them.

CR 708.2 is the whole idea: a face-down object has only the characteristics
the ability or rule that turned it face down lists, and those are its copiable
values. Everything here follows from asking "what turned it face down?":

- morph and megamorph (CR 702.37) and a bare "turn it face down" (CR 708.2a)
  list a 2/2 creature with no text, no name, no subtypes and no mana cost;
- disguise (CR 702.168a) and cloak (CR 701.58a) list the same with ward {2};
- manifest and cloak (CR 701.40b, 701.58b) are the ones that may later be
  turned face up for the card's mana cost, if it is a creature card.

A card exiled face down (CR 406.3a) is the other face-down object, and it has
no characteristics at all - no name, no types, and certainly not a 2/2 body.

The object's ``face_down_by`` records the answer, and the layer system
(CR 613.2b, layer 1b) asks this module what that answer makes the object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cr200_parts_of_a_card.characteristics import Characteristics
from ..kernel.enums import CardType, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject

if TYPE_CHECKING:
    from ..cr100_game_concepts.cr106_mana import ManaCost
    from ..kernel.game import Game
    from ..kernel.ids import PlayerId


#: CR 702.168a, 701.58a: the face-down characteristics these list include
#: ward {2}.
WARDED = frozenset({"Disguise", "Cloak"})

#: CR 701.40b, 701.58b: a manifested or cloaked permanent may be turned face
#: up for its card's mana cost. Morph and disguise have their own cost.
TURNED_UP_FOR_MANA_COST = frozenset({"Manifest", "Cloak"})

#: The zones where a face-down object is the 2/2 of CR 708.2. Anywhere else
#: it is a card exiled face down, which CR 406.3a gives no characteristics.
_FACE_DOWN_BODY_ZONES = frozenset({Zone.BATTLEFIELD, Zone.STACK})

_WARD_TWO: tuple = ()


def _ward_two() -> tuple:
    """Ward {2}, built once so every computation hands out the same ability."""
    global _WARD_TWO
    if not _WARD_TWO:
        from ..cr100_game_concepts.cr106_mana import ManaCost
        from ..cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
        from .cr702_keyword_impl import KeywordInstance, build

        _WARD_TWO = build(
            KeywordInstance(
                "Ward",
                cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{2}")),)),
            )
        )
    return _WARD_TWO


def face_down_characteristics(obj: GameObject) -> Characteristics:
    """What a face-down spell or permanent is (CR 708.2, 708.2a).

    CR 708.2: these are also its copiable values, which is why a copy of a
    face-down permanent is a nameless 2/2 rather than the card underneath.
    """
    from ..cr200_parts_of_a_card.cr205_typeline import TypeLine
    from ..cr100_game_concepts.cr106_mana import ManaCost

    return Characteristics(
        name="",
        mana_cost=ManaCost(()),
        has_mana_cost=False,
        type_line=TypeLine(types=CardType.CREATURE),
        abilities=_ward_two() if obj.face_down_by in WARDED else (),
        power=2,
        toughness=2,
        text="",
    )


def hidden_characteristics() -> Characteristics:
    """CR 406.3a: a card exiled face down has no characteristics."""
    return Characteristics(name="", abilities=())


def is_hidden(obj: GameObject) -> bool:
    """A face-down card outside the battlefield and the stack (CR 406.3a)."""
    return obj.face_down and obj.zone not in _FACE_DOWN_BODY_ZONES


# ---------------------------------------------------------------------------
# Putting cards onto the battlefield face down (CR 701.40, 701.58, 701.62)
# ---------------------------------------------------------------------------


def manifest(
    game: Game, player_id: PlayerId, card: GameObject, how: str = "Manifest"
) -> GameObject | None:
    """CR 701.40a / 701.58a: manifest or cloak one card.

    CR 708.3: it is turned face down *before* it enters, so the card's own
    enters abilities neither trigger nor apply - the move itself is told, and
    the permanent never exists face up. CR 701.40f: if the face-down object
    may not enter, the card stays where it was, unchanged. Returns the
    permanent, or None.
    """
    if not card.is_live or card.zone is Zone.BATTLEFIELD:
        return None
    permanent = game.move_object(card, Zone.BATTLEFIELD, to_player=player_id, face_down=how)
    if permanent is card or not permanent.is_permanent:
        return None
    permanent.controller = player_id
    game.log.record(
        game, f"{how.lower()}s {permanent}", kind="face-down", player=player_id
    )
    return permanent


def manifest_dread(game: Game, player_id: PlayerId) -> GameObject | None:
    """CR 701.62a: look at the top two, manifest one, the rest to the graveyard.

    The player picks which one. An agent may say through
    ``choose_manifest_dread(game, player, cards)``; without one, a creature
    card is preferred, because only a creature card can later be turned face
    up for its mana cost (CR 701.40b). CR 701.62b: "whenever you manifest
    dread" fires after the whole process, even if part of it was impossible.
    """
    player = game.player(player_id)
    looked = [game.objects[i] for i in player.library[:2] if i in game.objects]
    manifested = None
    if looked:
        chosen = None
        agent = game.agent_for(player_id)
        if agent is not None and hasattr(agent, "choose_manifest_dread"):
            chosen = agent.choose_manifest_dread(game, player_id, looked)
        if chosen not in looked:
            chosen = next(
                (
                    card
                    for card in looked
                    if game.printed_characteristics(card).has_type(CardType.CREATURE)
                ),
                looked[0],
            )
        manifested = manifest(game, player_id, chosen)
        for card in looked:
            if card is not chosen and card.is_live:
                game.move_object(card, Zone.GRAVEYARD, to_player=card.owner)
    game.emit(
        Event(
            EventKind.MANIFESTED_DREAD,
            object_id=manifested.id if manifested is not None else 0,
            player=player_id,
        )
    )
    return manifested


# ---------------------------------------------------------------------------
# Turning face up and face down
# ---------------------------------------------------------------------------


def mana_cost_to_turn_up(game: Game, obj: GameObject) -> ManaCost | None:
    """CR 701.40b / 701.58b: the cost of turning a manifested or cloaked
    permanent face up, or None if it cannot be turned up that way.

    Only a creature card with a mana cost qualifies - the question is asked
    of the card underneath, which ``printed_characteristics`` still shows.
    """
    if not obj.face_down or obj.face_down_by not in TURNED_UP_FOR_MANA_COST:
        return None
    card = game.printed_characteristics(obj)
    if not card.has_type(CardType.CREATURE) or not card.has_mana_cost:
        return None
    return card.mana_cost


def cannot_turn_face_up(game: Game, obj: GameObject) -> bool:
    """CR 701.40g / 701.58g: an instant or sorcery card stays face down."""
    card = game.printed_characteristics(obj)
    return bool(card.type_line.types & (CardType.INSTANT | CardType.SORCERY))


def turn_face_up(game: Game, obj: GameObject, *, megamorph: bool = False) -> bool:
    """Turn a face-down permanent face up (CR 708.8).

    It is the same object: counters, damage, attachments and every effect
    already applying to it stay, and because it has already entered nothing
    about entering happens again. CR 613.7f gives it a new timestamp. CR
    701.40g: an instant or sorcery card is revealed and left face down, and
    nothing triggers. CR 702.37b, applied as it turns up (CR 708.11): a
    megamorph cost paid leaves a +1/+1 counter.
    """
    if not obj.face_down or obj.zone is not Zone.BATTLEFIELD:
        return False
    if cannot_turn_face_up(game, obj):
        reveal(game, obj, "it can't be turned face up")
        return False
    obj.face_down = False
    obj.face_down_by = ""
    obj.timestamp = game.ids.timestamp()
    obj.invalidate()
    game.invalidate_characteristics()
    if megamorph:
        # The counter is placed after the characteristics come back, and
        # layer 7d has to be told, or the permanent keeps the power it was
        # computed with a moment ago.
        obj.add_counters("+1/+1", 1)
        obj.invalidate()
        game.invalidate_characteristics()
    game.log.record(game, f"{obj} is turned face up", kind="face-up", player=obj.controller)
    game.emit(Event(EventKind.TURNED_FACE_UP, object_id=obj.id, player=obj.controller))
    return True


def turn_face_down(game: Game, obj: GameObject) -> bool:
    """Turn a face-up permanent face down (CR 708.2a).

    CR 708.2b: one already face down is left exactly as it is. CR 712.16: a
    double-faced permanent cannot be turned face down. CR 613.7f: a new
    timestamp. The effect lists no characteristics, so it is a plain 2/2.
    """
    from ..kernel.enums import Layout
    from .cr707_faces import layout_of

    if obj.face_down or obj.zone is not Zone.BATTLEFIELD:
        return False
    if layout_of(obj) in (Layout.TRANSFORM, Layout.MODAL_DFC, Layout.MELD):
        return False
    obj.face_down = True
    obj.face_down_by = ""
    obj.timestamp = game.ids.timestamp()
    obj.invalidate()
    game.invalidate_characteristics()
    game.emit(Event(EventKind.TURNED_FACE_DOWN, object_id=obj.id, player=obj.controller))
    return True


# ---------------------------------------------------------------------------
# CR 708.9: revealing
# ---------------------------------------------------------------------------


def reveal(game: Game, obj: GameObject, why: str) -> None:
    """Show every player the card under a face-down object (CR 708.9)."""
    name = game.printed_characteristics(obj).name if obj.card is not None else ""
    game.log.record(
        game, f"face-down #{obj.id} is revealed: {name} ({why})", kind="reveal",
        player=obj.owner,
    )


def reveal_all(game: Game, owner: PlayerId | None = None) -> None:
    """CR 708.9: reveal every face-down spell and permanent - those a leaving
    player owns, or all of them at the end of the game."""
    for object_id in list(game.battlefield) + list(game.stack):
        obj = game.objects.get(object_id)
        if obj is None or not obj.face_down:
            continue
        if owner is not None and obj.owner != owner:
            continue
        reveal(game, obj, "the game ended" if owner is None else "its owner left")


__all__ = [
    "TURNED_UP_FOR_MANA_COST",
    "WARDED",
    "cannot_turn_face_up",
    "face_down_characteristics",
    "hidden_characteristics",
    "is_hidden",
    "mana_cost_to_turn_up",
    "manifest",
    "manifest_dread",
    "reveal",
    "reveal_all",
    "turn_face_down",
    "turn_face_up",
]
