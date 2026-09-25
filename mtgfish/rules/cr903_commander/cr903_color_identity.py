"""CR 903.4: colour identity as the game uses it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Color

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.ids import PlayerId


def commander_color_identity(game: Game, player_id: PlayerId) -> Color | None:
    """The combined colour identity of this player's commanders.

    CR 903.4a: established before the game begins, which the card records.
    CR 903.4f: ``None`` - undefined, not colourless - when the player has no
    commander; an ability that refers to it does nothing there.
    """
    player = game.player(player_id)
    commanders = getattr(player, "commanders", ())
    if not commanders:
        return None
    identity = Color.NONE
    for object_id in commanders:
        commander = game.objects.get(object_id)
        if commander is not None and commander.card is not None:
            identity |= Color(int(commander.card.color_identity))
    return identity
