"""CR 702.95: soulbond and paired creatures.

A pair is two creatures on the battlefield under one player's control. Each
records the other in ``GameObject.paired_with``. CR 702.95e ends the pair
the moment either creature stops being a creature, leaves the battlefield or
changes controller; ``is_still_paired`` answers that without changing
anything (it is asked from inside the layer system), and ``end_broken_pairs``
- run beside the state-based actions - clears what has ended, so a creature
that stops being a creature and becomes one again is not paired again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Zone
from ..kernel.ids import NO_OBJECT

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject
    from ..kernel.ids import PlayerId


def _pairable(game: Game, obj: GameObject | None, controller: PlayerId) -> bool:
    return (
        obj is not None
        and obj.is_live
        and obj.zone is Zone.BATTLEFIELD
        and obj.controller == controller
        and game.characteristics(obj).is_creature
    )


def is_still_paired(game: Game, obj: GameObject) -> bool:
    """Whether this creature is paired now (CR 702.95b, 702.95e)."""
    if obj.paired_with == NO_OBJECT:
        return False
    partner = game.objects.get(obj.paired_with)
    return (
        partner is not None
        and partner.paired_with == obj.id
        and _pairable(game, obj, obj.pair_controller)
        and _pairable(game, partner, obj.pair_controller)
    )


def partner_of(game: Game, obj: GameObject) -> GameObject | None:
    """The creature this one is paired with, if the pair still holds."""
    return game.objects.get(obj.paired_with) if is_still_paired(game, obj) else None


def pair(game: Game, first: GameObject, second: GameObject, controller: PlayerId) -> bool:
    """CR 702.95c/d: pair two creatures, if both are still creatures on the
    battlefield under this player's control and neither is already paired."""
    if first.id == second.id:
        return False
    if not (_pairable(game, first, controller) and _pairable(game, second, controller)):
        return False
    if is_still_paired(game, first) or is_still_paired(game, second):
        return False
    for one, other in ((first, second), (second, first)):
        one.paired_with = other.id
        one.pair_controller = controller
    game.invalidate_characteristics()
    game.log.record(game, f"{first} is paired with {second}", kind="pair", player=controller)
    return True


def end_broken_pairs(game: Game) -> bool:
    """CR 702.95e: clear every pair that has ended. Returns whether any did."""
    ended = False
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is not None and obj.paired_with != NO_OBJECT and not is_still_paired(game, obj):
            obj.paired_with = NO_OBJECT
            ended = True
    if ended:
        game.invalidate_characteristics()
    return ended


__all__ = ["end_broken_pairs", "is_still_paired", "pair", "partner_of"]
