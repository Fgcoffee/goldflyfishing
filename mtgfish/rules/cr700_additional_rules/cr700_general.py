"""CR 700: general terms that several cards share.

Most of CR 700 is vocabulary the rest of the engine reads directly - modes
(700.2), piles (700.3), "dies" (700.4), devotion (700.5), historic (700.6).
This module holds the terms that need the game to keep track of something as
it happens, or a calculation of their own:

* 700.8 a player's party - the largest set of one Cleric, Rogue, Warrior and
  Wizard their creatures can fill;
* 700.9 modified permanents, read by ``ObjectFilter.modified``;
* 700.10 permanents activated this turn, read by
  ``ObjectFilter.activated_this_turn``;
* 700.11 descending, announced as a DESCENDED event per permanent card;
* 700.12 outlaws and 700.16 worthy creatures, as filters;
* 700.13 committing a crime, announced as CRIME_COMMITTED;
* 700.14 expending, announced as EXPENDED once for each total crossed.

Announcing each as an event means "if you've committed a crime this turn",
"the number of times you descended this turn" and "whenever you expend 4"
are the ordinary event-this-turn condition, event count and trigger.
"""

from __future__ import annotations

from itertools import permutations
from typing import TYPE_CHECKING

from ..kernel.enums import Color, Supertype, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import ObjectKind
from ..kernel.ids import NO_OBJECT, NO_PLAYER, PlayerId
from ..kernel.query import ObjectFilter

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject

#: CR 700.8: the four party roles.
PARTY_ROLES = ("Cleric", "Rogue", "Warrior", "Wizard")

#: CR 700.12: the outlaw creature types.
OUTLAW_TYPES = ("Assassin", "Mercenary", "Pirate", "Rogue", "Warlock")


def outlaw_filter(**narrowing) -> ObjectFilter:
    """CR 700.12: an object with any outlaw creature type."""
    return ObjectFilter(subtypes_any=OUTLAW_TYPES, **narrowing)


def worthy_filter(**narrowing) -> ObjectFilter:
    """CR 700.16: legendary, not a Villain, and red and/or white."""
    return ObjectFilter(
        supertypes_all=Supertype.LEGENDARY,
        subtypes_none=("Villain",),
        colors_any=Color.RED | Color.WHITE,
        **narrowing,
    )


def party_size(game: Game, player_id: PlayerId) -> int:
    """CR 700.8: up to one creature in each role, each creature in one role.

    A creature with several of the types fills only one of them, so this is
    the largest matching between creatures and roles - four roles, so trying
    every assignment of roles to candidates is exact and cheap.
    """
    creatures = []
    for obj in game.permanents(player_id):
        chars = game.characteristics(obj)
        if not chars.is_creature:
            continue
        roles = {role for role in PARTY_ROLES if chars.has_subtype(role)}
        if roles:
            creatures.append(roles)
    best = 0
    for size in range(min(4, len(creatures)), 0, -1):
        for chosen in permutations(range(len(creatures)), size):
            for roles in permutations(PARTY_ROLES, size):
                if all(role in creatures[i] for i, role in zip(chosen, roles)):
                    return size
    return best


def is_modified(game: Game, obj: GameObject) -> bool:
    """CR 700.9: a counter on it, equipped, or enchanted by an Aura its
    controller controls."""
    if any(amount > 0 for amount in obj.counters.values()):
        return True
    for other in game.permanents():
        if other.attached_to != obj.id:
            continue
        chars = game.characteristics(other)
        if "Equipment" in chars.type_line.subtypes:
            return True
        if "Aura" in chars.type_line.subtypes and other.controller == obj.controller:
            return True
    return False


def was_activated_this_turn(obj: GameObject) -> bool:
    """CR 700.10: the source of an ability activated this turn - whether or
    not it still has that ability. The per-turn activation record says so."""
    return bool(obj.activations_this_turn)


# ---------------------------------------------------------------------------
# Events the game announces as they happen
# ---------------------------------------------------------------------------


def note(game: Game, event: Event) -> None:
    """Called for every event: announce the CR 700 happenings it amounts to."""
    kind = event.kind
    if kind is EventKind.ZONE_CHANGE and event.to_zone is Zone.GRAVEYARD:
        _descend(game, event)
    elif kind is EventKind.CAST_SPELL:
        spell = game.objects.get(event.object_id)
        if spell is not None:
            _expend(game, event.player, spell.mana_spent)
            _crime(game, event.player, spell.targets)
    elif kind is EventKind.PUT_ON_STACK:
        ability = game.objects.get(event.object_id)
        if ability is not None:
            _crime(game, event.player, ability.targets)


def _descend(game: Game, event: Event) -> None:
    """CR 700.11: a permanent card put into its owner's graveyard, from
    anywhere. A token is not a card, and neither is a copy."""
    obj = game.objects.get(event.object_id)
    if obj is None or obj.kind is not ObjectKind.CARD:
        return
    if not game.printed_characteristics(obj).type_line.is_permanent_type:
        return
    game.emit(Event(EventKind.DESCENDED, object_id=obj.id, player=obj.owner))


def _expend(game: Game, player_id: PlayerId, spent: int) -> None:
    """CR 700.14: each total of mana spent on spells this turn that paying
    this spell's cost carried the player past."""
    if player_id == NO_PLAYER or spent <= 0:
        return
    player = game.player(player_id)
    before = player.mana_spent_on_spells_this_turn
    player.mana_spent_on_spells_this_turn = before + spent
    for total in range(before + 1, before + spent + 1):
        game.emit(Event(EventKind.EXPENDED, player=player_id, amount=total))


def _crime(game: Game, player_id: PlayerId, targets) -> None:
    """CR 700.13: a spell or ability targeting an opponent, something an
    opponent controls, or a card in an opponent's graveyard."""
    if player_id == NO_PLAYER or not targets:
        return
    opponents = set(game.opponents(player_id))
    for chosen in targets:
        for target in chosen if isinstance(chosen, tuple) else (chosen,):
            if _is_criminal_target(game, target, opponents):
                game.emit(Event(EventKind.CRIME_COMMITTED, player=player_id))
                return


def _is_criminal_target(game: Game, target, opponents: set) -> bool:
    from ..kernel.ids import is_player_target, target_player

    if is_player_target(target):
        return target_player(target) in opponents
    if target == NO_OBJECT:
        return False
    obj = game.objects.get(target)
    if obj is None:
        return False
    if obj.zone is Zone.GRAVEYARD:
        return obj.owner in opponents
    if obj.zone in (Zone.BATTLEFIELD, Zone.STACK):
        return obj.controller in opponents
    return False


__all__ = [
    "OUTLAW_TYPES",
    "PARTY_ROLES",
    "is_modified",
    "note",
    "outlaw_filter",
    "party_size",
    "was_activated_this_turn",
    "worthy_filter",
]
