"""CR 722: preparation cards and the prepared designation.

A preparation card prints a second set of characteristics in an inset frame,
its "prepare spell" (CR 722.2a). The card is never cast as that spell
(CR 722.3); instead a permanent with a prepare spell can become prepared, and
while it is, a copy of the prepare spell waits in exile for its controller to
cast (CR 722.3c).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Layout, Zone
from ..kernel.gameobject import GameObject, ObjectKind
from ..kernel.ids import NO_OBJECT, ObjectId

if TYPE_CHECKING:
    from ..kernel.game import Game

#: The face that holds the prepare spell (CR 722.2): the inset frame, which
#: the card data prints as the second face.
PREPARE_FACE = 1


def has_prepare_spell(obj: GameObject) -> bool:
    """CR 722.2a: whether the object has a prepare spell to be prepared with."""
    card = obj.card
    return (
        card is not None
        and getattr(card, "layout", None) is Layout.PREPARE
        and len(getattr(card, "faces", ())) > PREPARE_FACE
    )


def is_prepared(game: Game, object_id: ObjectId) -> bool:
    return object_id in game.prepared_permanents


def become_prepared(game: Game, obj: GameObject) -> GameObject | None:
    """CR 722.3a/c: give a permanent the prepared designation.

    Only a permanent with a prepare spell can gain it, and not twice
    (CR 722.3a). As it does, its controller creates a copy of it in exile that
    has only the prepare spell's characteristics (CR 722.3c) and may cast that
    copy. Returns the copy, or ``None`` if nothing happened.
    """
    if obj.zone is not Zone.BATTLEFIELD or not obj.is_live:
        return None
    if not has_prepare_spell(obj) or is_prepared(game, obj.id):
        return None
    game.prepared_permanents.add(obj.id)
    copy = game.create_object(
        obj.card,
        obj.controller,
        Zone.EXILE,
        kind=ObjectKind.COPY,
        face_index=PREPARE_FACE,
    )
    copy.prepare_copy_of = obj.id
    # CR 722.3c: "the prepared permanent's controller may cast the copy" - the
    # standing permission legality reads, limited to the prepare spell's face.
    copy.playable_from_here_by = obj.controller
    copy.playable_face = PREPARE_FACE
    game.invalidate_characteristics()
    game.log.record(game, f"{obj} becomes prepared", kind="prepared", player=obj.controller)
    return copy


def become_unprepared(game: Game, object_id: ObjectId) -> None:
    """CR 722.3b: remove the designation. The copy's exception to CR 704.5e
    ends with it, so the next state-based action check removes the copy."""
    game.prepared_permanents.discard(object_id)


def keeps_its_prepare_copy(game: Game, copy: GameObject) -> bool:
    """CR 722.3c: the exception to CR 704.5e.

    The copy stays in exile for as long as the permanent it came from is on
    the battlefield and prepared, and no longer.
    """
    if copy.prepare_copy_of == NO_OBJECT or copy.zone is not Zone.EXILE:
        return False
    permanent = game.objects.get(copy.prepare_copy_of)
    return (
        permanent is not None
        and permanent.is_live
        and permanent.zone is Zone.BATTLEFIELD
        and is_prepared(game, permanent.id)
    )


def on_cast(game: Game, card_object: GameObject) -> None:
    """CR 722.3c with 601.2i: the permanent loses the designation as the copy
    becomes cast."""
    if card_object.prepare_copy_of != NO_OBJECT:
        become_unprepared(game, card_object.prepare_copy_of)


__all__ = [
    "PREPARE_FACE",
    "become_prepared",
    "become_unprepared",
    "has_prepare_spell",
    "is_prepared",
    "keeps_its_prepare_copy",
    "on_cast",
]
