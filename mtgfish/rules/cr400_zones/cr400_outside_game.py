"""Outside the game (CR 400.11), and the companion that waits there.

CR 400.11: outside the game is not a zone, so nothing here is a zone either.
Each player keeps the cards they own outside the game on
``Player.outside_game`` as card definitions, never as game objects. That is
how CR 400.11c holds without being checked anywhere: every spell and ability
in the engine acts on objects, and a card outside the game has none until
something brings it in.

Bringing a card in is the one door (CR 400.11b), and it enforces the two rules
that apply at the threshold:

* CR 108.3: the owner of a card brought in is the player who brought it in -
  the card becomes an object owned by that player, whoever sits next to them.
* CR 903.11a: in Commander a card can't come in if it shares a name with a
  card in that player's starting deck or one they own in the game, or if its
  colour identity strays outside their commander's.

CR 903.11 decides *what* may use that door in this format: only rules,
special actions and effects that say they work in Commander. A sideboard is
kept because a decklist may supply one and CR 400.11a says where it lives, but
nothing in the engine brings a sideboard card in; the companion special action
(CR 116.2g, 702.139d) is the only way in.

The companion's own deck-building condition ("each permanent card in your
starting deck has mana value 2 or less") is card text, and every companion's
is different. The engine has no generic predicate for it, so revealing a
companion (CR 103.2b) checks that the card *has* a companion ability and
otherwise trusts the decklist - which is said out loud in the deck report
rather than silently assumed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Color, Zone
from ..kernel.gameobject import ObjectKind

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject
    from ..kernel.ids import PlayerId

#: CR 116.2g / 702.139a: the flat cost of putting a companion into hand.
COMPANION_COST = "{3}"


def has_companion_ability(game: Game, card) -> bool:
    """Whether a card has a companion ability (CR 702.139a).

    Asked of the card's own abilities, as the ability provider supplies them,
    because CR 702.139a makes companion an ability that functions outside the
    game - there is no object for the layer system to consult. The front face
    is the one that counts outside the game (CR 712.8a).
    """
    if card is None:
        return False
    abilities = game.ability_provider.abilities_for(card, 0)
    return any(ability.keyword == "Companion" and not ability.unparsed for ability in abilities)


def reveal_companion(game: Game, player_id: PlayerId, card) -> bool:
    """CR 103.2b: reveal one card with companion from outside the game.

    The card stays outside the game (103.2b); revealing only records which
    card the special action may later bring in. A player reveals at most one,
    and only a card that has a companion ability. Returns whether it was
    revealed.
    """
    player = game.player(player_id)
    if player.companion is not None:
        return False
    if not has_companion_ability(game, card):
        game.log.record(
            game,
            f"{card} has no companion ability and cannot be revealed as a companion",
            kind="illegal",
            player=player_id,
        )
        return False
    if card not in player.outside_game:
        player.outside_game.append(card)
    player.companion = card
    game.log.record(
        game, f"{player.name} reveals {card} as their companion", kind="setup", player=player_id
    )
    return True


def _owned_names(game: Game, player_id: PlayerId) -> set[str]:
    """Names of the cards this player owns in the game right now (CR 903.11a)."""
    names: set[str] = set()
    for obj in game.objects.values():
        if obj.owner != player_id or obj.kind is not ObjectKind.CARD or not obj.is_live:
            continue
        name = getattr(obj.card, "name", "")
        if name:
            names.add(name)
    return names


def cannot_bring_in(game: Game, player_id: PlayerId, card) -> str | None:
    """Why this card may not come in from outside the game, or ``None``.

    CR 903.11a, all three clauses. A player with no commander has no commander
    colour identity (CR 903.4f), so the colour clause has nothing to compare
    against and does not bar anything.
    """
    from ..cr903_commander.cr903_color_identity import commander_color_identity

    player = game.player(player_id)
    name = getattr(card, "name", "")
    if card not in player.outside_game:
        return f"{name} is not outside the game for {player.name}"
    if name in player.starting_deck_names:
        return f"{name} shares a name with a card in the starting deck"
    if name in _owned_names(game, player_id):
        return f"{name} shares a name with a card already in the game"
    identity = commander_color_identity(game, player_id)
    card_identity = Color(int(getattr(card, "color_identity", Color.NONE)))
    if identity is not None and card_identity & ~identity:
        return f"{name} is outside the commander's color identity"
    return None


def bring_into_game(game: Game, player_id: PlayerId, card, zone: Zone) -> GameObject | None:
    """CR 400.11b: bring a card this player owns from outside the game in.

    CR 108.3: the new object is owned by the player who brought it in. Once
    in, it stays in (400.11b, 702.139c) - it is no longer outside the game, so
    nothing can bring it in a second time. Returns the new object, or ``None``
    if CR 903.11a forbids it.
    """
    reason = cannot_bring_in(game, player_id, card)
    if reason is not None:
        game.log.record(game, f"cannot bring in {card}: {reason}", kind="illegal", player=player_id)
        return None
    player = game.player(player_id)
    player.outside_game.remove(card)
    obj = game.create_object(card, player_id, zone)
    game.invalidate_characteristics()
    game.log.record(
        game,
        f"{player.name} brings {card} into the game from outside it",
        kind="outside-game",
        player=player_id,
    )
    return obj


__all__ = [
    "COMPANION_COST",
    "bring_into_game",
    "cannot_bring_in",
    "has_companion_ability",
    "reveal_companion",
]
