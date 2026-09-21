"""Game actions: the primitives everything else is built from (CR 701).

These are the verbs - destroy, exile, sacrifice, tap, damage, draw, discard,
mill. Effects, state-based actions, combat, and eventually the parser all reach
for the same functions here, which is what stops "destroy" meaning two
different things in two parts of the engine.

Each one enforces its own rules. ``destroy`` respects indestructible and
regeneration shields whether it was called by Doom Blade or by a state-based
action, so a card that says "destroy" cannot accidentally bypass protection
that the engine already knows about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import CardType, LossReason, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId

if TYPE_CHECKING:
    from ..kernel.game import Game


# ---------------------------------------------------------------------------
# Destruction and removal
# ---------------------------------------------------------------------------


def destroy(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> bool:
    """Destroy a permanent (CR 701.7).

    Returns whether it actually went to the graveyard. Two things stop it:
    indestructible (CR 702.12b), and a regeneration shield, which replaces the
    destruction with tapping, removing from combat, and clearing damage
    (CR 701.15).
    """
    # CR 110.1: a permanent is an object on the battlefield - and only the live
    # one. CR 400.7 leaves the pre-move object behind still reading the zone it
    # left, because last-known information needs it, so asking about the zone
    # alone accepts that husk: destroying an already-dead permanent a second
    # time moved it again and put a second copy of the card in the graveyard.
    if not obj.is_permanent:
        return False

    if is_indestructible(game, obj):
        game.log.record(game, f"{obj} is indestructible; not destroyed", kind="sba")
        return False

    from ..cr500_turn_structure.restrictions import Act, prohibited

    blocked = prohibited(game, Act.BE_DESTROYED, obj=obj)
    if blocked is not None:
        game.log.record(game, f"{obj} can't be destroyed ({blocked})", kind="restriction")
        return False

    if obj.regeneration_shields > 0:
        obj.regeneration_shields -= 1
        obj.tapped = True
        obj.damage = 0
        obj.dealt_deathtouch_damage = False
        remove_from_combat(game, obj)
        game.emit(Event(EventKind.REGENERATED, object_id=obj.id, source=source))
        return False

    game.emit(Event(EventKind.DESTROYED, object_id=obj.id, source=source))
    game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)
    return True


def is_indestructible(game: Game, obj: GameObject) -> bool:
    """CR 702.12b: indestructible permanents ignore destruction and lethal damage."""
    return game.characteristics(obj).has_keyword("Indestructible")


def sacrifice(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> bool:
    """Sacrifice a permanent (CR 701.17).

    Sacrificing is not destroying: indestructible does not stop it, regeneration
    does not replace it, and it cannot be prevented.
    """
    if not obj.is_permanent:  # CR 110.1, for the reason ``destroy`` gives.
        return False
    game.emit(
        Event(EventKind.SACRIFICED, object_id=obj.id, player=obj.controller, source=source)
    )
    game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)
    return True


def exile(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> GameObject:
    """Exile an object (CR 701.6).

    Records which object did the exiling (CR 607.2, 614.14). "Exile it, then
    you may play that card" is two abilities that only work together because
    the second can find what the first exiled, and once the card is in exile
    there is nothing about it that says how it got there.
    """
    game.emit(Event(EventKind.EXILED, object_id=obj.id, source=source))
    exiled = game.move_object(obj, Zone.EXILE)
    if source != NO_OBJECT:
        game.exiled_with.setdefault(source, []).append(exiled.id)
    return exiled


def exiled_with(game: Game, source: ObjectId) -> list[GameObject]:
    """The cards a given object exiled, for its linked ability (CR 607.2)."""
    # ``is_live`` matters: a card that left exile is superseded by a new object
    # but the husk keeps its old zone for last-known information, so a zone
    # check alone would keep reporting it as exiled for ever.
    return [
        game.objects[i]
        for i in game.exiled_with.get(source, ())
        if i in game.objects
        and game.objects[i].is_live
        and game.objects[i].zone is Zone.EXILE
    ]


def bounce(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> GameObject:
    """Return an object to its owner's hand."""
    game.emit(Event(EventKind.RETURNED_TO_HAND, object_id=obj.id, source=source))
    return game.move_object(obj, Zone.HAND, to_player=obj.owner)


def put_into_graveyard(game: Game, obj: GameObject) -> GameObject:
    """Move to the graveyard without destroying (CR 701.7's "put into" wording)."""
    return game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def tap(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> bool:
    """CR 701.21a. Tapping an already-tapped permanent does nothing at all."""
    if not obj.is_permanent or obj.tapped:  # CR 110.1
        return False
    obj.tapped = True
    game.emit(Event(EventKind.TAPPED, object_id=obj.id, player=obj.controller, source=source))
    return True


def untap(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> bool:
    """CR 701.26b. A "can't be untapped" effect beats any instruction to untap
    (CR 101.2), which is the whole of Winter Orb-style lockdown."""
    from ..cr500_turn_structure.restrictions import Act, prohibited

    if not obj.is_permanent or not obj.tapped:  # CR 110.1
        return False
    if prohibited(game, Act.UNTAP, obj=obj) is not None:
        return False
    obj.tapped = False
    game.emit(Event(EventKind.UNTAPPED, object_id=obj.id, player=obj.controller, source=source))
    return True


def attach(game: Game, attachment: GameObject, host: GameObject) -> bool:
    """Attach an Aura, Equipment, or Fortification (CR 701.3).

    Attaching gives the host a new timestamp (CR 613.7d), which is what makes a
    freshly-equipped creature's buffs apply after effects that were already
    there.
    """
    if attachment.attached_to == host.id:
        return False
    detach(game, attachment)
    attachment.attached_to = host.id
    host.attachments.append(attachment.id)
    attachment.timestamp = game.ids.timestamp()
    game.invalidate_characteristics()
    game.emit(Event(EventKind.ATTACHED, object_id=attachment.id, source=host.id))
    return True


def detach(game: Game, attachment: GameObject) -> bool:
    """CR 701.3b."""
    if attachment.attached_to == NO_OBJECT:
        return False
    host = game.objects.get(attachment.attached_to)
    if host is not None and attachment.id in host.attachments:
        host.attachments.remove(attachment.id)
    attachment.attached_to = NO_OBJECT
    game.invalidate_characteristics()
    game.emit(Event(EventKind.UNATTACHED, object_id=attachment.id))
    return True


def remove_from_combat(game: Game, obj: GameObject) -> None:
    """CR 506.4. A permanent removed from combat stops attacking or blocking.

    Also stops being blocked, and stops being attacked if it was a
    planeswalker or battle under attack. Whether anyone is still attacking is
    a condition continuous effects and triggers read, so the board has to be
    recomputed afterwards - "attacking creatures get +1/+0" stops applying the
    moment the creature stops attacking.
    """
    combat = getattr(game, "combat", None)
    if combat is None:
        return
    if not (
        obj.id in combat.attacking
        or obj.id in combat.blocking
        or obj.id in combat.attacking_permanent.values()
        or obj.id in combat.was_blocked
    ):
        return
    combat.remove(obj.id)
    game.log.record(game, f"{obj} is removed from combat", kind="combat")
    game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# Counters (CR 122)
# ---------------------------------------------------------------------------


def add_counters(
    game: Game, obj: GameObject, kind: str, amount: int, *, source: ObjectId = NO_OBJECT
) -> int:
    """Put counters on an object (CR 122.1).

    CR 107.1b: an amount worked out from a calculation that came out negative
    is zero, not a removal - "put X +1/+1 counters on it" where X went below
    zero puts none on, it does not take any off.
    """
    if amount <= 0:
        return 0

    # CR 614: Doubling Season and friends replace the counter-placement event
    # rather than adding a second batch, which is why two of them multiply
    # rather than each doubling the original.
    if game.has_replacements:
        prospective = game.replace(
            Event(
                EventKind.COUNTER_ADDED,
                object_id=obj.id,
                player=obj.controller,
                source=source,
                amount=amount,
                data=(kind,),
            )
        )
        if prospective is None:
            return 0
        amount = prospective.amount
        if amount <= 0:
            return 0

    added = obj.add_counters(kind, amount)
    game.invalidate_characteristics()
    game.emit(
        Event(
            EventKind.COUNTER_ADDED,
            object_id=obj.id,
            player=obj.controller,
            source=source,
            amount=added,
            data=(kind,),
        )
    )
    return added


def remove_counters(
    game: Game, obj: GameObject, kind: str, amount: int, *, source: ObjectId = NO_OBJECT
) -> int:
    removed = obj.remove_counters(kind, amount)
    if removed:
        game.invalidate_characteristics()
        game.emit(
            Event(
                EventKind.COUNTER_REMOVED,
                object_id=obj.id,
                player=obj.controller,
                source=source,
                amount=removed,
                data=(kind,),
            )
        )
    return removed


# ---------------------------------------------------------------------------
# Damage and life (CR 119, CR 120)
# ---------------------------------------------------------------------------


def excess_damage(game: Game, target: GameObject, amount: int) -> int:
    """How much of ``amount`` would be excess damage (CR 120.10).

    Measured against what the permanent can still take *before* the damage is
    dealt: lethal damage for a creature, loyalty for a planeswalker, defense
    for a battle. A permanent with more than one of those card types uses the
    greatest of the amounts, which is the reading that makes an excess trigger
    fire as rarely as the rule intends.

    The rule speaks of what one or more sources dealt *together*, and this
    engine deals damage one source at a time even where the rules have it
    simultaneous. Each call measures against what is left after the previous
    one, so the excess reported across a combat damage step still totals the
    right amount - it simply arrives split across the events rather than in
    one number.
    """
    if amount <= 0:
        return 0
    chars = game.characteristics(target)
    capacities: list[int] = []
    if chars.is_creature:
        capacities.append(max(0, (chars.toughness or 0) - target.damage))
    if chars.has_type(CardType.PLANESWALKER):
        capacities.append(target.counter_count("loyalty"))
    if chars.has_type(CardType.BATTLE):
        capacities.append(target.counter_count("defense"))
    if not capacities:
        return 0
    # The greatest capacity gives the smallest excess.
    return max(0, amount - max(capacities))


def deal_damage(
    game: Game,
    target: GameObject | PlayerId,
    amount: int,
    *,
    source: ObjectId = NO_OBJECT,
    source_controller: PlayerId = NO_PLAYER,
    deathtouch: bool = False,
    lifelink: bool = False,
    combat: bool = False,
    is_commander_source: bool = False,
) -> int:
    """Deal damage (CR 120).

    What damage *does* depends entirely on what receives it: a creature has it
    marked until cleanup, a player loses life, a planeswalker loses loyalty
    counters, and a battle loses defense counters. Nothing is destroyed here -
    lethal damage kills via a state-based action later (CR 704.5g), and that
    delay is observable.

    CR 120.8: a source dealing 0 damage deals no damage at all, so nothing
    that watches for damage sees anything and no replacement effect has an
    event to replace. CR 107.1b covers the other half: damage is never
    negative, so a calculation that went below zero deals none either.
    """
    if amount <= 0:
        return 0

    # CR 614/615: prevention and damage-modifying effects apply before the
    # damage is dealt. A shield that stops all of it means no damage event ever
    # happens, so nothing that triggers on damage sees anything.
    if game.has_replacements:
        prospective = game.replace(
            Event(
                EventKind.COMBAT_DAMAGE_DEALT if combat else EventKind.DAMAGE_DEALT,
                object_id=target.id if not isinstance(target, int) else NO_OBJECT,
                player=PlayerId(target) if isinstance(target, int) else NO_PLAYER,
                source=source,
                source_controller=source_controller,
                amount=amount,
            )
        )
        if prospective is None:
            return 0
        amount = prospective.amount
        if amount <= 0:
            return 0

    source_obj = game.objects.get(source)
    source_chars = game.characteristics(source_obj) if source_obj is not None else None

    # CR 702.16e: damage from a source with the stated quality is prevented.
    if not isinstance(target, int) and _protected_from(game, target, source_obj):
        game.log.record(game, f"{target} has protection; damage prevented", kind="protection")
        return 0

    if isinstance(target, int):
        return _damage_player(
            game,
            PlayerId(target),
            amount,
            source=source,
            source_controller=source_controller,
            lifelink=lifelink,
            combat=combat,
            is_commander_source=is_commander_source,
        )

    chars = game.characteristics(target)

    # CR 702.90b / 702.75a: wither and infect deal their damage to creatures as
    # -1/-1 counters instead. It is still damage - it still triggers everything
    # that watches for damage - but nothing is ever marked on the creature.
    if source_chars is not None and chars.is_creature:
        if source_chars.has_keyword("Wither") or source_chars.has_keyword("Infect"):
            # CR 120.10 before the counters land, for the same reason as below.
            excess = excess_damage(game, target, amount)
            add_counters(game, target, "-1/-1", amount, source=source)
            game.emit(
                Event(
                    EventKind.COMBAT_DAMAGE_DEALT if combat else EventKind.DAMAGE_DEALT,
                    object_id=target.id,
                    player=target.controller,
                    source=source,
                    source_controller=source_controller,
                    amount=amount,
                    data=(excess,),
                )
            )
            if lifelink and source_controller != NO_PLAYER:
                gain_life(game, source_controller, amount, source=source)
            return amount

    # CR 120.10: measured before the damage is dealt, and carried on the event
    # so an ability that checks for excess damage has something to read.
    excess = excess_damage(game, target, amount)

    if chars.has_type(CardType.PLANESWALKER):
        remove_counters(game, target, "loyalty", amount, source=source)
    elif chars.has_type(CardType.BATTLE):
        remove_counters(game, target, "defense", amount, source=source)
    elif chars.is_creature:
        target.damage += amount
        if deathtouch:
            target.dealt_deathtouch_damage = True
    else:
        # Damage to a permanent that is neither creature, planeswalker, nor
        # battle has no effect (CR 120.3).
        return 0

    game.emit(
        Event(
            EventKind.COMBAT_DAMAGE_DEALT if combat else EventKind.DAMAGE_DEALT,
            object_id=target.id,
            player=target.controller,
            source=source,
            source_controller=source_controller,
            amount=amount,
            data=(excess,),
        )
    )
    if lifelink and source_controller != NO_PLAYER:
        gain_life(game, source_controller, amount, source=source)
    return amount


def _damage_player(
    game: Game,
    player_id: PlayerId,
    amount: int,
    *,
    source: ObjectId,
    source_controller: PlayerId,
    lifelink: bool,
    combat: bool,
    is_commander_source: bool,
) -> int:
    """Damage to a player causes that much life loss (CR 120.3a)."""
    player = game.player(player_id)

    # CR 800.4e: "If combat damage would be assigned to a player who has left
    # the game, that damage isn't assigned." A creature can still be attacking
    # a player who died earlier in the combat, and this is the moment that
    # would otherwise have drained a life total nobody has any more.
    if combat and player.has_lost:
        return 0

    # CR 702.90c: infect damages players with poison counters instead of life.
    source_obj = game.objects.get(source)
    source_chars = game.characteristics(source_obj) if source_obj is not None else None
    if source_chars is not None and source_chars.has_keyword("Infect"):
        add_poison(game, player_id, amount)
        game.emit(
            Event(
                EventKind.COMBAT_DAMAGE_DEALT if combat else EventKind.DAMAGE_DEALT,
                player=player_id,
                source=source,
                source_controller=source_controller,
                amount=amount,
            )
        )
        if lifelink and source_controller != NO_PLAYER:
            gain_life(game, source_controller, amount, source=source)
        return amount

    player.lose_life(amount)

    # CR 903.10: combat damage from a commander is tracked separately, and 21
    # from any single commander is lethal on its own.
    if combat and is_commander_source and source != NO_OBJECT:
        origin = game.commander_identity(source)
        total = player.take_commander_damage(origin, amount)
        game.emit(
            Event(
                EventKind.COMMANDER_DAMAGE_DEALT,
                player=player_id,
                source=source,
                source_controller=source_controller,
                amount=total,
            )
        )

    game.emit(
        Event(
            EventKind.COMBAT_DAMAGE_DEALT if combat else EventKind.DAMAGE_DEALT,
            player=player_id,
            source=source,
            source_controller=source_controller,
            amount=amount,
        )
    )
    game.emit(Event(EventKind.LIFE_LOST, player=player_id, amount=amount, source=source))
    if lifelink and source_controller != NO_PLAYER:
        gain_life(game, source_controller, amount, source=source)
    return amount


def gain_life(
    game: Game, player_id: PlayerId, amount: int, *, source: ObjectId = NO_OBJECT
) -> int:
    """Gain life (CR 119.3).

    CR 119.9 and CR 119.10: gaining zero life is not a life-gain event, so it
    triggers nothing and there is nothing for a replacement effect to replace.

    Life gain is replaceable (CR 614), which is the whole of Rain of Gore: "if
    a spell or ability would cause its controller to gain life, that player
    loses that much life instead". That replacement can turn this event into a
    life *loss*, and when it does the gain never happened - so no "whenever you
    gain life" trigger sees anything. Lifelink routes through here for exactly
    that reason rather than adding life directly.
    """
    if amount <= 0:
        return 0

    if game.has_replacements:
        prospective = game.replace(
            Event(EventKind.LIFE_GAINED, player=player_id, amount=amount, source=source)
        )
        if prospective is None:
            return 0
        if prospective.kind is EventKind.LIFE_LOST:
            lose_life(game, player_id, prospective.amount, source=source)
            return 0
        amount = prospective.amount
        if amount <= 0:
            return 0

    gained = game.player(player_id).gain_life(amount)
    if gained:
        game.emit(
            Event(EventKind.LIFE_GAINED, player=player_id, amount=gained, source=source)
        )
    return gained


def lose_life(
    game: Game, player_id: PlayerId, amount: int, *, source: ObjectId = NO_OBJECT
) -> int:
    lost = game.player(player_id).lose_life(amount)
    if lost:
        game.emit(Event(EventKind.LIFE_LOST, player=player_id, amount=lost, source=source))
    return lost


def add_poison(game: Game, player_id: PlayerId, amount: int) -> int:
    """Give a player poison counters (CR 122.1, 104.3c).

    CR 107.1b: never a negative number, so a calculation that came out below
    zero gives none rather than taking poison away.
    """
    if amount <= 0:
        return 0
    game.player(player_id).poison += amount
    game.emit(Event(EventKind.POISON_ADDED, player=player_id, amount=amount))
    return amount


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def discard(game: Game, obj: GameObject, *, source: ObjectId = NO_OBJECT) -> GameObject:
    """CR 701.8."""
    game.emit(
        Event(EventKind.DISCARDED, object_id=obj.id, player=obj.owner, source=source)
    )
    return game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)


def mill(game: Game, player_id: PlayerId, count: int, *, source: ObjectId = NO_OBJECT) -> int:
    """CR 701.13. Milling an empty library does not cause a loss on its own."""
    player = game.player(player_id)
    milled = 0
    for _ in range(count):
        if not player.library:
            break
        game.move_object(game.objects[player.library[0]], Zone.GRAVEYARD, to_player=player_id)
        milled += 1
    if milled:
        game.emit(
            Event(EventKind.MILLED, player=player_id, amount=milled, source=source)
        )
    return milled


def create_token(
    game: Game,
    card: object,
    controller: PlayerId,
    *,
    tapped: bool = False,
    source: ObjectId = NO_OBJECT,
) -> GameObject:
    """CR 111.1: a token enters the battlefield as a new object."""
    from ..kernel.gameobject import ObjectKind

    obj = game.create_object(card, controller, Zone.BATTLEFIELD, kind=ObjectKind.TOKEN)
    obj.tapped = tapped
    obj.entered_battlefield_turn = game.turn
    obj.summoning_sick = True
    game.invalidate_characteristics()
    game.emit(
        Event(EventKind.TOKEN_CREATED, object_id=obj.id, player=controller, source=source)
    )
    game.emit(
        Event(
            EventKind.ENTERS_BATTLEFIELD,
            object_id=obj.id,
            player=controller,
            from_zone=None,
        )
    )
    return obj


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def player_loses(game: Game, player_id: PlayerId, reason: LossReason) -> None:
    game.player_loses(player_id, reason)


# ---------------------------------------------------------------------------
# Randomness (CR 705, 706)
# ---------------------------------------------------------------------------


def flip_coin(game: Game, player_id: PlayerId) -> bool:
    """Flip a coin (CR 705.1). True is heads.

    Drawn from the game's seeded RNG rather than anywhere else, because a
    replay reconstructs the game from its seed and a coin flip from a different
    source would diverge on the spot.
    """
    result = game.rng.random() < 0.5
    game.log.record(
        game,
        f"{game.player(player_id).name} flips {'heads' if result else 'tails'}",
        kind="coin",
        player=player_id,
    )
    return result


def roll_die(game: Game, player_id: PlayerId, sides: int) -> int:
    """Roll a die with ``sides`` faces (CR 706.1). Returns 1..sides."""
    if sides < 1:
        return 0
    result = game.rng.randint(1, sides)
    game.log.record(
        game,
        f"{game.player(player_id).name} rolls {result} on a d{sides}",
        kind="dice",
        player=player_id,
    )
    return result


def roll_dice(game: Game, player_id: PlayerId, count: int, sides: int) -> list[int]:
    """Roll several dice, ignoring the lowest results is the caller's business.

    CR 107.1b: a count that came out negative rolls nothing.
    """
    return [roll_die(game, player_id, sides) for _ in range(max(0, count))]


def _protected_from(game: Game, target, source_obj) -> bool:
    """CR 702.16: whether ``target`` has protection from ``source_obj``.

    The "from a quality" half. Protection from red does not stop a white spell,
    and treating it as a blanket ban - which is what the engine used to do -
    silently over-protects every creature that has it.
    """
    if source_obj is None:
        return False
    from ..kernel.matching import matches

    for ability in game.characteristics(target).keyword_abilities("Protection"):
        quality = ability.quality
        if quality is None:
            return True  # Protection from everything, if a card ever says so.
        if matches(game, source_obj, quality, allow_stale=True):
            return True
    return False


def protected_from(game: Game, target, source_obj) -> bool:
    """Public form of the protection check, for combat and targeting."""
    return _protected_from(game, target, source_obj)


def linked_objects(game, source_id: int, link_id: int = 0) -> list:
    """CR 607.2: what the other half of a linked ability refers to.

    "Exile target creature" and "return the exiled card" are two abilities on
    one object, and the second one means *the cards this ability's partner
    exiled* - not every card in exile, and not cards exiled by a different
    copy of the same card. The link is the exile event, recorded when it
    happened, which is why a blinked permanent's linked ability finds nothing:
    it is a new object and its abilities are newly linked to nothing.
    """
    recorded = game.exiled_with.get(source_id)
    if not recorded:
        return []
    out = []
    for entry in recorded:
        object_id, entry_link = entry if isinstance(entry, tuple) else (entry, 0)
        if link_id and entry_link and entry_link != link_id:
            continue
        obj = game.objects.get(object_id)
        if obj is not None and obj.is_live:
            out.append(obj)
    return out


def record_link(game, source_id: int, object_id: int, link_id: int = 0) -> None:
    """Remember that this ability moved this object (CR 607.2)."""
    game.exiled_with.setdefault(source_id, []).append((object_id, link_id))
