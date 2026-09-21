"""The stack, and resolving what is on it (CR 405, 608).

The stack is last-in-first-out and holds spells and abilities. Only the top
object ever resolves, and only when every player has passed priority in
succession (CR 117.4).

The rule worth spelling out is CR 608.2b, the fizzle: a spell or ability with
targets is countered on resolution if *all* of its targets have become illegal.
If only some have, it resolves and does as much as it can. Getting this
backwards - fizzling on any illegal target - silently changes how every
removal-heavy game plays out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .abilities import AbilityKind
from .enums import CardType, Zone
from .events import Event, EventKind
from .gameobject import GameObject, ObjectKind
from .resolve import Resolution, execute

if TYPE_CHECKING:
    from .game import Game


def top(game: Game) -> GameObject | None:
    """The object that will resolve next, or None if the stack is empty."""
    if not game.stack:
        return None
    return game.objects.get(game.stack[-1])


def resolve_top(game: Game) -> bool:
    """Resolve the top object of the stack (CR 608).

    Returns whether anything resolved.
    """
    obj = top(game)
    if obj is None:
        return False

    game.stack.pop()
    game.log.record(game, f"Resolving {obj}", kind="resolve", player=obj.controller)
    game.log.push()
    try:
        if obj.is_ability_on_stack:
            _resolve_ability(game, obj)
        else:
            _resolve_spell(game, obj)
    finally:
        game.log.pop()
    game.invalidate_characteristics()
    return True


# ---------------------------------------------------------------------------
# Abilities
# ---------------------------------------------------------------------------


def _resolve_ability(game: Game, obj: GameObject) -> None:
    ability = obj.ability
    if ability is None:
        _cease(game, obj)
        return

    # CR 603.4: an intervening-if clause is checked a second time here, and the
    # ability does nothing if it has become false.
    if ability.kind is AbilityKind.TRIGGERED and ability.trigger is not None:
        from .cr603_triggers import check_intervening_if

        source = game.objects.get(obj.source)
        if source is not None and not check_intervening_if(game, source, ability.trigger):
            game.log.record(game, "Intervening 'if' no longer true; ability does nothing")
            _cease(game, obj)
            return

    if _all_targets_illegal(game, obj):
        game.emit(Event(EventKind.FIZZLED, object_id=obj.id, player=obj.controller))
        game.log.record(game, "All targets illegal; ability is countered (CR 608.2b)")
        _cease(game, obj)
        return

    resolution = Resolution(
        game=game,
        source=obj.source or obj.id,
        controller=obj.controller,
        stack_object=obj,
        targets=obj.targets,
        x_value=obj.x_value,
        event_amount=getattr(obj.trigger_event, "amount", 0) or 0,
    )
    execute(resolution, ability.effects)
    game.emit(
        Event(EventKind.ABILITY_RESOLVED, object_id=obj.id, player=obj.controller)
    )
    _cease(game, obj)


def _cease(game: Game, obj: GameObject) -> None:
    """An ability that has finished resolving simply stops existing (CR 608.2m)."""
    game.objects.pop(obj.id, None)


# ---------------------------------------------------------------------------
# Spells
# ---------------------------------------------------------------------------


def _resolve_spell(game: Game, obj: GameObject) -> None:
    chars = game.characteristics(obj)

    if _all_targets_illegal(game, obj):
        game.emit(Event(EventKind.FIZZLED, object_id=obj.id, player=obj.controller))
        game.log.record(game, "All targets illegal; spell is countered (CR 608.2b)")
        game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)
        return

    is_permanent = bool(chars.type_line.is_permanent_type)

    if is_permanent:
        # CR 608.3: a permanent spell resolves by becoming a permanent. Its
        # spell abilities are not executed - they are the permanent's abilities
        # now, and any enters-the-battlefield trigger fires from there.
        permanent = game.move_object(obj, Zone.BATTLEFIELD, to_player=obj.controller)
        permanent.controller = obj.controller
        permanent.x_value = obj.x_value
        # It got here by resolving as a spell, which is what "if you cast it"
        # asks. A permanent put onto the battlefield never passes through
        # here and so is left with the default.
        permanent.was_cast = True
        _apply_enters_with_counters(game, permanent)
        _attach_aura_on_entry(game, obj, permanent)
        game.emit(
            Event(EventKind.SPELL_RESOLVED, object_id=permanent.id, player=obj.controller)
        )
        return

    resolution = Resolution(
        game=game,
        source=obj.id,
        controller=obj.controller,
        stack_object=obj,
        targets=obj.targets,
        x_value=obj.x_value,
    )
    for ability in chars.abilities:
        if ability.kind is AbilityKind.SPELL:
            execute(resolution, ability.effects)

    game.emit(Event(EventKind.SPELL_RESOLVED, object_id=obj.id, player=obj.controller))
    # CR 608.2m: an instant or sorcery goes to its owner's graveyard as the
    # final part of its resolution.
    game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)


def _attach_aura_on_entry(game: Game, spell: GameObject, permanent: GameObject) -> None:
    """CR 303.4: "An Aura enters the battlefield attached to an object or player."

    The Aura spell targeted its host at announcement (CR 303.4a), so the host
    is the target it was cast with. Without this an Aura resolved attached to
    nothing and CR 704.5m put it straight into the graveyard - every Aura cast
    from hand was a dead card.
    """
    from .cr601_casting import aura_target_effect

    chars = game.characteristics(permanent)
    if aura_target_effect(chars) is None:
        return
    chosen = spell.targets[0] if spell.targets else ()
    host_id = next((t for t in chosen), None)
    if host_id is None:
        return
    host = game.objects.get(host_id)
    if host is None or host.zone is not Zone.BATTLEFIELD:
        # CR 608.2b: the target is gone, so the Aura has nothing to attach to.
        # It stays on the battlefield for the state-based actions to handle.
        return
    from . import actions

    actions.attach(game, permanent, host)


def _apply_enters_with_counters(game: Game, permanent: GameObject) -> None:
    """Loyalty and defense arrive as counters, not as a stored number.

    CR 306.5b and 310.4: a planeswalker enters with loyalty counters equal to
    its printed loyalty, and a battle with defense counters. Modelling them as
    counters rather than a field is what makes damage, proliferate, and
    +1/+1-style interactions work without special cases.
    """
    chars = game.characteristics(permanent)
    if chars.has_type(CardType.PLANESWALKER) and chars.loyalty is not None:
        if permanent.counter_count("loyalty") == 0:
            permanent.add_counters("loyalty", chars.loyalty)
    if chars.has_type(CardType.BATTLE) and chars.defense is not None:
        if permanent.counter_count("defense") == 0:
            permanent.add_counters("defense", chars.defense)
    game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# Targeting legality (CR 608.2b)
# ---------------------------------------------------------------------------


def _all_targets_illegal(game: Game, obj: GameObject) -> bool:
    """CR 608.2b: countered only when *every* target is illegal.

    A spell with no targets at all never fizzles, and a spell with two targets
    where one died still resolves and does what it can to the survivor.
    """
    if not obj.targets:
        return False

    from .matching import matches

    ability = obj.ability
    if ability is not None:
        effects = ability.effects
    else:
        effects = tuple(
            effect
            for a in game.characteristics(obj).abilities
            if a.kind is AbilityKind.SPELL
            for effect in a.effects
        )

    targeting_effects = [node for e in effects for node in e.walk() if node.is_targeted]
    if not targeting_effects:
        return False

    any_legal = False
    for index, effect in enumerate(targeting_effects):
        if index >= len(obj.targets):
            break
        for object_id in obj.targets[index]:
            if object_id < 0:
                # CR 115.4: a player target. A player is a legal target for as
                # long as they are in the game - they cannot be destroyed,
                # bounced or given hexproof by leaving the battlefield.
                from .ids import target_player

                if not game.player(target_player(object_id)).has_lost:
                    any_legal = True
                continue
            candidate = game.objects.get(object_id)
            if candidate is None:
                continue
            if effect.targets is None or matches(
                game,
                candidate,
                effect.targets,
                source=obj.source or obj.id,
                controller=obj.controller,
            ):
                any_legal = True
                break
        if any_legal:
            break
    return not any_legal


# ---------------------------------------------------------------------------
# Putting things on the stack
# ---------------------------------------------------------------------------


def push_ability(
    game: Game,
    source: GameObject,
    ability,
    *,
    targets: tuple = (),
    x_value: int = 0,
) -> GameObject:
    """Put an activated or triggered ability on the stack (CR 113.7).

    The ability on the stack is an object in its own right and exists
    independently of its source, so destroying the source in response does not
    remove it.
    """
    stack_object = GameObject(
        id=game.ids.object_id(),
        kind=ObjectKind.ABILITY,
        owner=source.controller,
        controller=source.controller,
        zone=Zone.STACK,
        timestamp=game.ids.timestamp(),
        ability=ability,
        source=source.id,
        targets=targets,
        x_value=x_value,
    )
    game.objects[stack_object.id] = stack_object
    game.stack.append(stack_object.id)
    game.emit(
        Event(
            EventKind.PUT_ON_STACK,
            object_id=stack_object.id,
            player=source.controller,
            source=source.id,
        )
    )
    return stack_object
