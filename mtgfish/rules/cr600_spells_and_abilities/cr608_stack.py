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

from ..kernel.enums import CardType, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject, ObjectKind
from .abilities import AbilityKind
from .resolve import Resolution, execute

if TYPE_CHECKING:
    from ..kernel.game import Game


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
        # CR 110.2b: a stolen permanent spell becomes a permanent the thief
        # controls, but its controller *by default* is still the player who
        # put the spell on the stack. ``move_object`` sets both from the
        # current controller, so the theft became permanent: layer 2 (CR
        # 613.1b) recomputes the controller from the default, and when the
        # control effect ended the permanent went to the thief rather than
        # back to its caster.
        permanent.base_controller = obj.base_controller
        _carry_effects_onto_the_permanent(game, obj, permanent)
        permanent.x_value = obj.x_value
        # CR 702.33e and its kin: "if it was kicked", "if no mana was spent to
        # cast it", "if its surge cost was paid" are asked by the permanent's
        # own abilities about the spell it was. Left on the spell, every one
        # of them read the new object's defaults and was never true.
        permanent.additional_costs_paid = obj.additional_costs_paid
        permanent.mana_spent = obj.mana_spent
        permanent.alternative_cost_paid = obj.alternative_cost_paid
        # It got here by resolving as a spell, which is what "if you cast it"
        # asks. A permanent put onto the battlefield never passes through
        # here and so is left with the default.
        permanent.was_cast = True
        _apply_enters_with_counters(game, permanent)
        _attach_aura_on_entry(game, obj, permanent)
        if obj.enters_tapped_and_attacking:
            _enter_tapped_and_attacking(game, obj, permanent)
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
    # final part of its resolution - unless it was cast with alternative
    # characteristics that say otherwise. CR 715.3d exiles a resolved
    # Adventure so its controller may cast the creature later, and CR 720.3d
    # shuffles a resolved Omen into its owner's library.
    from ..cr300_card_types.cr300_card_types import CastMode, resolution_zone

    if not obj.is_live:
        # CR 608.2m moves the spell only if it is still there to move. One
        # that has already left the stack - "exile this spell", "shuffle this
        # card into its owner's library" - is a new object elsewhere, and
        # moving the old one again put the card in two zones at once.
        return
    # Compared against None rather than tested for truth: Zone.LIBRARY is 0,
    # so "or Zone.GRAVEYARD" silently sends every Omen to the graveyard.
    destination = resolution_zone(CastMode(obj.cast_mode))
    if destination is None:
        destination = Zone.GRAVEYARD
    moved = game.move_object(obj, destination, to_player=obj.owner)
    if CastMode(obj.cast_mode) is CastMode.ADVENTURE:
        # CR 715.3d: for as long as the card remains exiled its controller may
        # play it - the creature half, not the Adventure again. Recorded on
        # the exiled object, so CR 400.7 ends the permission the moment the
        # card moves anywhere else.
        moved.playable_from_here_by = obj.controller
        moved.playable_face = 0


def _enter_tapped_and_attacking(
    game: Game, spell: GameObject, permanent: GameObject
) -> None:
    """CR 702.190b: the permanent enters tapped and attacking what the creature
    returned to pay its cost was attacking.

    CR 506.3a: it was never declared as an attacker. ``enter_attacking``
    decides whether it can be one at all, and what it attacks.
    """
    from ..cr500_turn_structure.cr506_combat import enter_attacking

    permanent.tapped = True
    enter_attacking(game, permanent, like=spell.enters_attacking_like)


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
    from ..cr100_game_concepts import actions

    actions.attach(game, permanent, host)


def _carry_effects_onto_the_permanent(
    game: Game, spell: GameObject, permanent: GameObject
) -> None:
    """CR 112.4: an effect on a permanent spell follows it onto the battlefield.

    CR 400.7 makes the permanent a new object, and a settled effect (CR 611.2c)
    names the objects it applies to by id - so every effect that named the
    spell stopped applying the moment it resolved. A creature spell made white
    entered the battlefield black again.

    This is the one place CR 400.7's "it remembers nothing" has an exception
    written into the rules, so it is done by naming the new object as well as
    the old rather than by loosening what counts as the same object: ``matches``
    is also the targeting predicate, and making a resolved permanent answer to
    a filter that named the spell would quietly change target legality too.
    """
    from dataclasses import replace

    changed = False
    for continuous in game.continuous_effects:
        spec = continuous.effect.targets
        if spec is None or not spec.specific or spell.id not in spec.specific:
            continue
        continuous.effect = replace(
            continuous.effect,
            targets=replace(
                spec,
                specific=(*spec.specific, permanent.id),
                zones=frozenset(spec.zones) | {Zone.BATTLEFIELD},
            ),
        )
        changed = True
    if changed:
        game.invalidate_characteristics()


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

    from ..kernel.matching import matches

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

    # CR 700.2c: the targets belong to the modes that were chosen, so the
    # unchosen ones are not walked - their slots do not exist.
    from .cr601_casting import targeted_nodes

    targeting_effects = targeted_nodes(effects, obj.chosen_modes or None)
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
                from ..kernel.ids import target_player

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
    chosen_modes: tuple[int, ...] = (),
) -> GameObject:
    """Put an activated or triggered ability on the stack (CR 113.7).

    The ability on the stack is an object in its own right and exists
    independently of its source, so destroying the source in response does not
    remove it.

    The modes travel with it, as its targets do (CR 602.2b, 700.2a): they were
    chosen as it was put here, and the resolver has no way to ask later.
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
        chosen_modes=chosen_modes,
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
