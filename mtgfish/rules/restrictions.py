"""Prohibitions and permissions (CR 101.2, 101.3, 601.3).

CR 101.2 is one sentence and it governs the whole game: "When a rule or effect
allows or directs something to happen, and another effect states that it can't
happen, the 'can't' effect takes precedence."

That has to be a general mechanism rather than a check bolted onto each
individual effect. If "can't attack" is a special case inside combat and
"can't be countered" is a special case inside the stack, then the twentieth
prohibition gets forgotten and nobody notices, because a missing prohibition
silently *allows* something rather than crashing.

So every "may I?" question in the engine routes through ``prohibited`` here,
and adding a new prohibition is data rather than code.

CR 101.3 is the mirror image: an instruction that is impossible is ignored,
with no effect unless the card says otherwise. That is why every action
primitive returns what it actually managed to do rather than assuming it
worked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from .query import ALWAYS, Condition, ObjectFilter, PlayerFilter

if TYPE_CHECKING:
    from .game import Game
    from .gameobject import GameObject


class Act(IntEnum):
    """Things a rule or effect can forbid.

    Deliberately exhaustive rather than minimal: a prohibition the engine has
    no name for is one it cannot enforce, and the failure mode is permissive.
    """

    # -- playing and casting ------------------------------------------------
    CAST_SPELL = 0
    PLAY_LAND = 1
    #: CR 307.1 timing, granted rather than forbidden: "you may cast sorcery
    #: spells as though they had flash". Its own act because a permission to
    #: *ignore timing* is not a permission to cast - the spell still has to be
    #: castable for every other reason.
    CAST_AS_THOUGH_FLASH = 5
    ACTIVATE_ABILITY = 2
    ACTIVATE_MANA_ABILITY = 3
    ACTIVATE_LOYALTY_ABILITY = 4

    # -- combat -------------------------------------------------------------
    ATTACK = 20
    ATTACK_PLAYER = 21
    ATTACK_PLANESWALKER = 22
    BLOCK = 23
    BE_BLOCKED = 24

    # -- the stack ----------------------------------------------------------
    BE_COUNTERED = 40
    BE_TARGETED = 41
    COPY = 42

    # -- permanents ---------------------------------------------------------
    UNTAP = 60
    TAP = 61
    BE_DESTROYED = 62
    BE_SACRIFICED = 63
    BE_REGENERATED = 64
    ENTER_BATTLEFIELD = 65
    LEAVE_BATTLEFIELD = 66
    HAVE_COUNTERS_PLACED = 67
    BE_EXILED = 68
    CHANGE_CONTROL = 69
    PHASE_OUT = 70

    # -- players ------------------------------------------------------------
    DRAW_CARD = 90
    GAIN_LIFE = 91
    LOSE_LIFE = 92
    SEARCH_LIBRARY = 93
    WIN_GAME = 94
    LOSE_GAME = 95
    BE_DEALT_DAMAGE = 96
    SHUFFLE = 97
    CAST_MORE_THAN_ONE_SPELL = 98
    UNTAP_DURING_UNTAP_STEP = 99
    #: "Your life total can't change" - not a ban on gaining and a ban on
    #: losing but one prohibition on the total moving at all, which is why it
    #: is a single act rather than a pair.
    LIFE_TOTAL_CHANGE = 101
    #: "Play with the top card of your library revealed" / "you may look at
    #: the top card of your library any time". A permission to see, which the
    #: play-from-the-top permissions are written alongside but separate from.
    LOOK_AT_TOP_CARD = 102
    #: CR 402.2 / 514.1: discarding down to the maximum hand size in cleanup.
    #: Expressed as something a player may be *permitted* to skip, because
    #: "you have no maximum hand size" is a permission, not a prohibition.
    DISCARD_TO_HAND_SIZE = 100


@dataclass(frozen=True, slots=True)
class Restriction:
    """One "can't" in force.

    ``subject`` and ``players`` say who or what it applies to; ``None`` means
    the restriction's own source. ``condition`` gates it, for the "can't attack
    unless..." shape.
    """

    act: Act
    subject: ObjectFilter | None = None
    players: PlayerFilter | None = None
    source: ObjectId = NO_OBJECT
    controller: PlayerId = NO_PLAYER
    condition: Condition = ALWAYS
    #: A restriction that names something specific: "can't be blocked by
    #: creatures with power 2 or less" constrains the *other* participant.
    counterpart: ObjectFilter | None = None
    text: str = ""
    #: ``rules.enums.Duration``, for a prohibition registered by a resolved
    #: spell rather than regenerated from a static ability. Held as an int to
    #: keep this module from importing the enum, and only consulted for
    #: standing entries - one rebuilt from a permanent ends when the permanent
    #: does and needs no duration of its own.
    duration: int = 0
    #: The turn the prohibition started on; see ``ContinuousEffect``.
    created_turn: int = 0

    def __str__(self) -> str:
        return self.text or f"can't {self.act.name.lower().replace('_', ' ')}"

    @property
    def act_phrase(self) -> str:
        """The act alone, with any leading negation stripped.

        The same Restriction is used for a prohibition and for a permission,
        so a caller composing "X can't ..." or "X may ..." needs the verb
        without a "can't" already attached - otherwise the explainer says
        "can't can't be regenerated".
        """
        text = self.text or self.act.name.lower().replace("_", " ")
        for prefix in ("can't ", "cannot ", "may not "):
            if text.lower().startswith(prefix):
                return text[len(prefix) :]
        return text


@dataclass(slots=True)
class RestrictionSet:
    """Every prohibition currently in force.

    Rebuilt from static abilities the same way continuous effects are, so a
    prohibition ends the instant its source stops existing.
    """

    entries: list[Restriction] = field(default_factory=list)

    def add(self, restriction: Restriction) -> None:
        self.entries.append(restriction)

    def clear(self) -> None:
        self.entries.clear()


def prohibited(
    game: Game,
    act: Act,
    *,
    obj: GameObject | None = None,
    player: PlayerId = NO_PLAYER,
    counterpart: GameObject | None = None,
) -> Restriction | None:
    """Whether ``act`` is forbidden right now, and by what.

    Returns the restriction so the caller can log *why* something was not
    allowed, which matters enormously when a simulated game does something
    surprising.
    """
    from .conditions import holds
    from .matching import matches, resolve_players

    for restriction in _active(game):
        if restriction.act is not act:
            continue

        if restriction.subject is not None:
            if obj is None:
                continue
            if not matches(
                game,
                obj,
                restriction.subject,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue
        elif restriction.source != NO_OBJECT and obj is not None:
            if obj.id != restriction.source:
                continue

        if restriction.players is not None:
            target_player = player
            if target_player == NO_PLAYER and obj is not None:
                target_player = obj.controller
            if target_player == NO_PLAYER:
                continue
            allowed = resolve_players(
                game, restriction.players, controller=restriction.controller
            )
            if target_player not in allowed:
                continue

        if restriction.counterpart is not None:
            if counterpart is None:
                continue
            if not matches(
                game,
                counterpart,
                restriction.counterpart,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue

        if not restriction.condition.is_always:
            if not holds(
                game,
                restriction.condition,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue

        return restriction

    return None


def allowed(game: Game, act: Act, **kwargs) -> bool:
    """Convenience inverse of ``prohibited``."""
    return prohibited(game, act, **kwargs) is None


def _active(game: Game) -> list[Restriction]:
    """Restrictions in force, rebuilt from live static abilities.

    Regenerated rather than persisted, for the same reason continuous effects
    are (CR 611.3): a prohibition from a permanent that has left the
    battlefield is not a prohibition any more.
    """
    cached = game.restrictions_cache
    if cached is not None and game.restrictions_epoch == game.epoch:
        return cached

    from .abilities import AbilityKind
    from .effects import EffectKind


    out: list[Restriction] = list(game.standing_restrictions)
    # CR 604.3: a static ability can function from a zone other than the
    # battlefield. Split second is the reason this loop includes the stack -
    # its prohibition applies precisely while the spell is *on* the stack, and
    # a battlefield-only scan would never see it.
    scope = list(game.battlefield) + list(game.stack)
    for object_id in scope:
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.RESTRICTION:
                    continue
                for restriction in effect.restrictions:
                    out.append(
                        Restriction(
                            act=restriction.act,
                            subject=restriction.subject or effect.targets,
                            players=restriction.players or effect.players,
                            source=obj.id,
                            controller=obj.controller,
                            condition=ability.static_condition,
                            counterpart=restriction.counterpart,
                            text=restriction.text or effect.text,
                        )
                    )

    game.restrictions_cache = out
    game.restrictions_epoch = game.epoch
    return out


def permitted(
    game: Game,
    act: Act,
    *,
    obj: GameObject | None = None,
    player: PlayerId = NO_PLAYER,
) -> Restriction | None:
    """Whether an effect *permits* ``act`` that the rules would otherwise deny.

    The mirror image of ``prohibited``, and the half that was missing. CR 113.6
    permissions - "you may cast sorceries as though they had flash", "you may
    cast this from your graveyard" - are not prohibitions with a "not" in
    front; they are their own layer, and without them the rules stay maximally
    restrictive and Vedalken Orrery does nothing at all.

    Returns the permission so the caller can log *why* something unusual was
    allowed, for the same reason ``prohibited`` returns the restriction.
    """
    from .conditions import holds
    from .matching import matches, resolve_players

    for permission in _active_permissions(game):
        if permission.act is not act:
            continue

        if permission.subject is not None:
            if obj is None:
                continue
            if not matches(
                game,
                obj,
                permission.subject,
                source=permission.source,
                controller=permission.controller,
            ):
                continue

        if permission.players is not None and player != NO_PLAYER:
            allowed = resolve_players(
                game, permission.players, controller=permission.controller
            )
            if player not in allowed:
                continue

        if not holds(
            game,
            permission.condition,
            source=permission.source,
            controller=permission.controller,
        ):
            continue
        return permission
    return None


def _active_permissions(game: Game) -> list[Restriction]:
    """Permissions in force, rebuilt from live static abilities.

    Rebuilt rather than persisted for the same reason prohibitions are
    (CR 611.3): a permission from a permanent that has left the battlefield is
    not a permission any more.
    """
    cached = game.permissions_cache
    if cached is not None and game.permissions_epoch == game.epoch:
        return cached

    from .abilities import AbilityKind
    from .effects import EffectKind

    out: list[Restriction] = list(game.standing_permissions)
    for object_id in list(game.battlefield) + list(game.stack):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.PERMISSION:
                    continue
                for permission in effect.restrictions:
                    out.append(
                        Restriction(
                            act=permission.act,
                            subject=permission.subject or effect.targets,
                            players=permission.players or effect.players,
                            source=obj.id,
                            controller=obj.controller,
                            condition=ability.static_condition,
                            counterpart=permission.counterpart,
                            text=permission.text or effect.text,
                        )
                    )

    game.permissions_cache = out
    game.permissions_epoch = game.epoch
    return out


def register_standing_permission(game: Game, permission: Restriction) -> Restriction:
    """Add a permission not tied to a permanent's static ability.

    Used by resolved spells with a duration - "you may cast spells this turn
    as though they had flash" - which persist independently of any source.
    """
    from dataclasses import replace

    if not permission.created_turn:
        permission = replace(permission, created_turn=game.turn)
    game.standing_permissions.append(permission)
    game.permissions_epoch = -1
    return permission


def register_standing(game: Game, restriction: Restriction) -> Restriction:
    """Add a prohibition that is not tied to a permanent's static ability.

    Used by resolved spells with a duration ("creatures can't attack this
    turn"), which persist independently of any source (CR 611.2b).
    """
    from dataclasses import replace

    if not restriction.created_turn:
        restriction = replace(restriction, created_turn=game.turn)
    game.standing_restrictions.append(restriction)
    game.restrictions_cache = None
    return restriction
