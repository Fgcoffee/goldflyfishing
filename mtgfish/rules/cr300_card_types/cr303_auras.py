"""CR 303.4f, 303.4g: an Aura entering the battlefield other than by
resolving as an Aura spell.

Its player chooses something it can legally enchant as it enters - this is
not targeting, so hexproof and shroud don't stop it, but the enchant ability
and protection (CR 702.16c) do. With nothing it could enchant, it stays in
the zone it was in; from the stack it goes to its owner's graveyard instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Zone
from ..kernel.ids import NO_PLAYER, is_player_target, player_target, target_player

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


def _enchant_quality(chars):
    """The quality of the Aura's enchant ability, or ``None`` when it is not
    an Aura or the enchant ability was not read (it then keeps the old
    behaviour)."""
    if not chars.has_subtype("Aura"):
        return None
    for ability in chars.abilities:
        if ability.keyword.lower() == "enchant" and ability.quality is not None:
            return ability.quality
    return None


def governs_entry(game: Game, aura: GameObject) -> bool:
    """Whether CR 303.4f decides where this object is attached as it enters."""
    return _enchant_quality(game.characteristics(aura)) is not None


def legal_hosts(game: Game, aura: GameObject, player_id) -> list[int]:
    """What the Aura could enchant as it enters under ``player_id``: objects
    on the battlefield and players, encoded as target ids."""
    from ..cr100_game_concepts.actions import player_protected_from, protected_from
    from ..cr600_spells_and_abilities.cr601_casting import enchantable_scope
    from ..kernel.matching import find
    from ..kernel.query import PlayerFilter, PlayerScope

    quality = _enchant_quality(game.characteristics(aura))
    if quality is None:
        return []
    if isinstance(quality, PlayerFilter):
        opponents_only = enchantable_scope(quality) is PlayerScope.TARGET_OPPONENT
        return [
            player_target(player.id)
            for player in game.living_players
            if not (opponents_only and player.id == player_id)
            and not player_protected_from(game, player.id, aura)
        ]
    return [
        host.id
        for host in find(game, quality, source=aura.id, controller=player_id)
        # CR 303.4d: an Aura can't enchant itself.
        if host.id != aura.id
        and host.zone is Zone.BATTLEFIELD
        and not protected_from(game, host, aura)
    ]


def attach_as_it_enters(game: Game, aura: GameObject, hosts: list[int]) -> None:
    """CR 303.4f: the Aura's controller chooses one of ``hosts`` and the Aura
    is attached to it. The choice is made like a target choice, so an agent's
    judgement of who should get a Curse applies, but nothing is targeted."""
    from ..cr100_game_concepts import actions

    if not hosts:
        return
    picked = hosts[0]
    agent = game.agent_for(aura.controller)
    chooser = getattr(agent, "choose_targets", None)
    if chooser is not None:
        answer = chooser(game, aura.controller, aura.id, [list(hosts)])
        chosen = next((group[0] for group in answer or () if group), None)
        if chosen in hosts:
            picked = chosen
    if is_player_target(picked):
        player_id = target_player(picked)
        if player_id != NO_PLAYER:
            actions.attach_to_player(game, aura, player_id)
        return
    host = game.objects.get(picked)
    if host is not None:
        actions.attach(game, aura, host)
