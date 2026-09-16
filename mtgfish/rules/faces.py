"""Double-faced cards, transforming, and copying (CR 707, 712).

Two rules here run directly against instincts formed elsewhere in the engine.

**CR 712.18: transforming does not create a new object.** Everywhere else, an
object that changes what it is becomes a new object with no memory (CR 400.7).
A transforming permanent is the exception - it keeps its id, its counters, its
damage, its auras, and every effect that was applying to it. Implementing
transform as "make a new object with the other face" would silently drop all of
that, and the card would look fine.

**CR 707.2: a copy takes only *copiable values*.** Those are the printed
characteristics as modified by other copy effects and by text-changing effects,
and nothing else (CR 613.2). Not counters, not +1/+1 from an Anthem, not the
type change from Opalescence. A copy of a pumped creature is not pumped. Since
copiable values are exactly what ``printed_characteristics`` returns, copying
is a matter of pointing at the right face rather than of filtering a result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import CardType, Layout, Zone
from .events import Event, EventKind
from .gameobject import GameObject, ObjectKind

if TYPE_CHECKING:
    from .game import Game


#: CR 712.9: only these can transform. A meld card's back is half an oversized
#: face and is reached by melding, not by turning over.
TRANSFORMABLE_LAYOUTS = frozenset(
    {Layout.TRANSFORM, Layout.MODAL_DFC, Layout.DOUBLE_FACED_TOKEN}
)


def layout_of(obj: GameObject) -> Layout:
    return getattr(obj.card, "layout", Layout.NORMAL)


def face_count(obj: GameObject) -> int:
    faces = getattr(obj.card, "faces", ())
    return len(faces)


def can_transform(game: Game, obj: GameObject) -> bool:
    """CR 712.9, 712.10, 712.16.

    Three separate refusals: the card must be a kind that transforms at all, it
    must have another face, and that face must not be an instant or sorcery -
    an MDFC with a spell on the back does not transform into a permanent that
    cannot exist on the battlefield.
    """
    if obj.zone is not Zone.BATTLEFIELD:
        return False
    if layout_of(obj) not in TRANSFORMABLE_LAYOUTS:
        return False
    if face_count(obj) < 2:
        return False
    if obj.face_down:
        # CR 712.16: a double-faced permanent cannot be face down in the first
        # place, so there is nothing to turn over.
        return False

    other = other_face(obj)
    if other is None:
        return False
    if other.type_line.types & (CardType.INSTANT | CardType.SORCERY):
        return False
    return True


def other_face(obj: GameObject):
    faces = getattr(obj.card, "faces", ())
    if len(faces) < 2:
        return None
    return faces[1 - obj.face_index] if obj.face_index < 2 else faces[0]


def transform(game: Game, obj: GameObject) -> bool:
    """Turn a double-faced permanent over (CR 701.28, 712.18).

    Deliberately mutates the object in place. CR 712.18 says a transforming
    permanent does *not* become a new object, so everything attached to it,
    every counter on it, and every continuous effect targeting it carries
    straight over. This is the one place in the engine where changing what
    something is does not allocate a new id.
    """
    if not can_transform(game, obj):
        return False

    obj.face_index = 1 - obj.face_index
    game.invalidate_characteristics()
    game.log.record(
        game,
        f"{obj} transforms into {game.characteristics(obj).name}",
        kind="transform",
        player=obj.controller,
    )
    game.emit(
        Event(EventKind.TRANSFORMED, object_id=obj.id, player=obj.controller)
    )
    return True


# ---------------------------------------------------------------------------
# Which face is played, and how (CR 712.11 - 712.14)
# ---------------------------------------------------------------------------


def castable_face_indices(game: Game, obj: GameObject) -> list[int]:
    """Faces of a card in hand that may be *cast* (as opposed to played).

    CR 712.11: a double-faced spell is cast front face up by default. A modal
    double-faced card offers both, minus any face that is a land - those are
    played, not cast (CR 712.12).
    """
    card = obj.card
    faces = getattr(card, "faces", ())
    if not faces:
        return [0]

    layout = layout_of(obj)
    if layout is Layout.MODAL_DFC:
        return [
            index
            for index, face in enumerate(faces)
            if not (face.type_line.types & CardType.LAND) and face.has_mana_cost
        ]
    if layout is Layout.SPLIT or layout is Layout.ADVENTURE:
        return [
            index for index, face in enumerate(faces) if face.has_mana_cost
        ]
    # Transforming and meld cards are cast with their front face (CR 712.11).
    return [0]


def playable_land_face_indices(game: Game, obj: GameObject) -> list[int]:
    """Faces that may be played as a land (CR 712.12).

    An MDFC with a land on the back is a land drop, not a cast, and it enters
    with that face up.
    """
    faces = getattr(obj.card, "faces", ())
    if not faces:
        return []
    if layout_of(obj) is Layout.MODAL_DFC:
        return [
            index
            for index, face in enumerate(faces)
            if face.type_line.types & CardType.LAND
        ]
    return [0] if faces[0].type_line.types & CardType.LAND else []


def default_battlefield_face(obj: GameObject) -> int:
    """CR 712.14: entering from anywhere but the stack means front face up."""
    return 0


# ---------------------------------------------------------------------------
# Copying (CR 707)
# ---------------------------------------------------------------------------


def copiable_characteristics(game: Game, obj: GameObject):
    """The values a copy of this object takes (CR 707.2, 613.2).

    The printed characteristics of the face that is currently up (CR 707.8),
    as modified by other copy effects and text-changing effects - and nothing
    from layers 2 through 7. That is why a copy of a creature carrying three
    +1/+1 counters and a Giant Growth is just a plain copy of the card.
    """
    return game.printed_characteristics(obj)


def copy_permanent(
    game: Game, copier: GameObject, original: GameObject
) -> bool:
    """Make one permanent a copy of another (CR 707.1).

    Registered as a layer 1 continuous effect rather than written into the
    object, because copying is a continuous effect and has to be re-applied
    every time characteristics are computed - and because a later copy effect
    must be able to override this one by timestamp.
    """
    from .effects import Effect, EffectKind
    from .game import ContinuousEffect
    from .layers import layer_for

    effect = Effect(
        EffectKind.COPY_PERMANENT,
        copy_source=original.id,
        text=f"copy of {game.characteristics(original).name}",
    )
    game.continuous_effects.append(
        ContinuousEffect(
            effect=effect,
            source=copier.id,
            controller=copier.controller,
            timestamp=game.ids.timestamp(),
            layer=int(layer_for(effect)),
            duration=0,
        )
    )
    game.invalidate_characteristics()
    game.emit(
        Event(EventKind.COPIED, object_id=copier.id, source=original.id)
    )
    return True


def copy_spell(game: Game, original: GameObject, controller=None) -> GameObject | None:
    """Put a copy of a spell or ability onto the stack (CR 707.10).

    CR 707.10: the copy is *not cast*. Nothing that triggers on casting sees
    it, which is the entire reason storm and cascade copies do not chain.
    The copy takes both the characteristics and the choices - modes, targets,
    the value of X - made for the original.
    """
    if original.zone is not Zone.STACK:
        return None

    owner = controller if controller is not None else original.controller
    copy = GameObject(
        id=game.ids.object_id(),
        kind=ObjectKind.COPY if original.ability is None else ObjectKind.ABILITY,
        owner=owner,
        controller=owner,
        zone=Zone.STACK,
        card=original.card,
        face_index=original.face_index,
        timestamp=game.ids.timestamp(),
        ability=original.ability,
        source=original.source,
        # CR 707.2: choices made when casting are copied too.
        targets=original.targets,
        chosen_modes=original.chosen_modes,
        x_value=original.x_value,
    )
    game.objects[copy.id] = copy
    game.stack.append(copy.id)
    game.invalidate_characteristics()
    game.log.record(
        game, f"A copy of {original} is put onto the stack", kind="copy", player=owner
    )
    game.emit(Event(EventKind.COPIED, object_id=copy.id, source=original.id))
    return copy
