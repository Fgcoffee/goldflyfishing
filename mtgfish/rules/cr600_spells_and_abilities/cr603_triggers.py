"""Triggered abilities (CR 603).

A triggered ability does not go on the stack when it triggers. It triggers, is
noted, and then goes on the stack the next time a player would receive priority
(CR 603.3b) - which is why a creature can die, its dies-trigger can be noted,
and the trigger still resolves even though the creature is long gone.

The intervening-if clause (CR 603.4) is the fiddliest part: a condition written
before the effect is checked *twice*, once when the ability would trigger and
again as it resolves. It is not the same as an "if" inside the effect, which is
checked only on resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, NamedTuple

from ..kernel.enums import Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from .abilities import Ability, AbilityKind, TriggerCondition

if TYPE_CHECKING:
    from ..kernel.game import Game


class PendingTrigger(NamedTuple):
    """An ability that has triggered and is waiting for the stack (CR 603.3).

    ``controller`` is captured *here*, when the ability triggers, and not read
    off the source when the ability finally goes on the stack. CR 603.3d puts
    the ability under the player who controlled its source at the moment it
    triggered, and by the time it reaches the stack that source may be gone -
    a token that ceased to exist, or a permanent owned by a player who has
    since left the game (CR 800.4a).

    Looking the controller up late gave those abilities no controller at all.
    An ability controlled by nobody then created tokens owned by nobody, and
    the first one of those to leave the battlefield took the whole game down
    with "GRAVEYARD is player-owned; a player is required" - a crash a long
    way from the trigger that caused it.
    """

    source: ObjectId
    ability: Ability
    event: Event
    controller: PlayerId


#: Zones scanned for triggered abilities. The library is deliberately excluded:
#: no printed triggered ability functions from a library, and scanning four
#: 99-card libraries on every event would dominate the profile.
_WATCHED_SHARED_ZONES = (Zone.BATTLEFIELD, Zone.COMMAND, Zone.STACK, Zone.EXILE)
_WATCHED_PLAYER_ZONES = (Zone.GRAVEYARD, Zone.HAND)


def collect_triggers(game: Game, event: Event) -> None:
    """Note every ability that triggers on ``event`` (CR 603.2).

    Triggers are appended to ``game.pending_triggers`` in APNAP order
    (CR 101.4, 603.3b), which is the order their controllers put them on the
    stack.
    """
    # A loop shortcut applies many cycles at once. Every trigger that fires
    # once per cycle is already part of that cycle and already in what is
    # being scaled, so letting it see the lump sum would count it again.
    if game.suppress_triggers:
        return

    found: list[tuple[GameObject, Ability]] = []

    _collect_delayed(game, event, found)

    for object_id, ability in _watchers(game).get(event.kind, ()):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        if obj.zone not in ability.trigger.functions_in:
            continue
        if not _has_the_ability_now(game, obj, ability):
            continue
        if not condition_met(game, obj, ability.trigger, event):
            continue
        found.append((obj, ability))

    if event.looks_back_in_time:
        _collect_departed_token(game, event, found)

    if not found:
        return

    # APNAP: the active player's triggers go on the stack first, so they
    # resolve last (CR 603.3b).
    order = {player: index for index, player in enumerate(game.apnap_order())}
    found.sort(key=lambda pair: (order.get(pair[0].controller, 99), pair[0].timestamp))

    for obj, ability in found:
        # The controller is read now, while the source is still here to ask.
        pending = PendingTrigger(obj.id, ability, event, obj.controller)
        game.pending_triggers.append(pending)
        # CR 603.2b: something may say this ability triggers an extra time.
        # Both instances are separate triggers - they go on the stack
        # independently and can be responded to between them.
        for _ in range(_extra_triggers(game, obj, event)):
            game.pending_triggers.append(pending)


def _collect_departed_token(
    game: Game, event: Event, found: list[tuple[GameObject, Ability]]
) -> None:
    """A token's own "when this dies" (CR 603.10a).

    A card that dies arrives in a graveyard, where the index finds the new
    object and looks back at the old one. A token never arrives anywhere
    (CR 111.7): it is in no zone the index covers, so its own leaves-the-
    battlefield abilities were never looked for - Kiki-Jiki's copies, populated
    tokens and clone tokens of anything with a death trigger all died silently.
    """
    from ..kernel.gameobject import ObjectKind

    token = game.objects.get(event.object_id)
    if token is None or token.kind is not ObjectKind.TOKEN or token.phased_out:
        return
    if any(obj.id == token.id for obj, _ in found):
        return
    for ability in game.printed_characteristics(token).abilities:
        if ability.kind is not AbilityKind.TRIGGERED or ability.unparsed:
            continue
        trigger = ability.trigger
        if trigger is None or event.kind not in _kinds_watched(trigger):
            continue
        if condition_met(game, token, trigger, event):
            found.append((token, ability))


def _extra_triggers(game: Game, obj: GameObject, event: Event) -> int:
    """How many additional times this object's abilities trigger (CR 603.2b).

    Read from static abilities on the battlefield rather than registered,
    because the answer depends on the permanent whose ability triggered and
    on what caused the event - neither of which is known until it happens.
    """
    from ..kernel.matching import matches
    from .abilities import AbilityKind
    from .effects import EffectKind

    extra = 0
    for source_id in game.battlefield:
        source = game.objects.get(source_id)
        if source is None or source.phased_out:
            continue
        for ability in game.characteristics(source).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.EXTRA_TRIGGER:
                    continue
                if effect.targets is not None and not matches(
                    game,
                    obj,
                    effect.targets,
                    source=source.id,
                    controller=source.controller,
                    allow_stale=True,
                ):
                    continue
                cause = _subject_of(game, event)
                if effect.trigger_cause is not None and (
                    cause is None
                    or not matches(
                        game,
                        cause,
                        effect.trigger_cause,
                        source=source.id,
                        controller=source.controller,
                        allow_stale=True,
                    )
                ):
                    continue
                extra += max(1, effect.amount.constant)
    return extra


def _collect_delayed(
    game: Game, event: Event, found: list[tuple[GameObject, Ability]]
) -> None:
    """Fire any delayed triggered abilities waiting on this event (CR 603.7).

    A delayed trigger is not an ability of any object - it was set up by a
    resolving spell ("at the beginning of the next end step, sacrifice it") and
    exists on its own. CR 603.7b: it fires once, the *next* time its event
    happens, unless it was given a stated duration.

    CR 603.7a matters too: it cannot fire for an event that happened before it
    was created, which falls out of only ever checking newly-emitted events.
    """
    if not game.delayed_triggers:
        return

    from ..kernel.gameobject import ObjectKind

    for delayed in list(game.delayed_triggers):
        if delayed.expired:
            continue
        if event.kind not in delayed.trigger.event_kinds:
            continue
        if not _right_phase(delayed.trigger, event):
            continue

        source = game.objects.get(delayed.source)
        if source is None:
            # The ability exists independently of whatever created it, so a
            # missing source is not a reason to skip. Stand in a bare object so
            # the trigger still has a controller.
            source = GameObject(
                id=game.ids.object_id(),
                kind=ObjectKind.ABILITY,
                owner=delayed.controller,
                controller=delayed.controller,
                zone=Zone.COMMAND,
            )
            game.objects[source.id] = source

        if delayed.trigger.subject is not None:
            # CR 603.7: a delayed ability can name what the event must be
            # about - "when the permanent that exiled it leaves the
            # battlefield" is about one permanent and no other. Nothing
            # checked it, so such a trigger fired on every event of its kind.
            # Matched against last known information, because the object the
            # event is about has usually just gone (CR 603.6e).
            from ..kernel.matching import matches

            subject = _subject_of(game, event)
            if subject is None or not matches(
                game,
                subject,
                delayed.trigger.subject,
                controller=delayed.controller,
                allow_stale=True,
            ):
                continue

        if delayed.trigger.players is not None and event.player != NO_PLAYER:
            from ..kernel.matching import resolve_players

            if event.player not in resolve_players(
                game, delayed.trigger.players, controller=delayed.controller
            ):
                continue

        ability = Ability(
            AbilityKind.TRIGGERED,
            effects=delayed.effects,
            trigger=delayed.trigger,
            text=delayed.trigger.text or "delayed trigger",
        )
        found.append((source, ability))
        if not delayed.repeating:
            delayed.expired = True

    game.delayed_triggers = [d for d in game.delayed_triggers if not d.expired]


def _has_the_ability_now(game: Game, obj: GameObject, ability) -> bool:
    """"As long as [condition], it has [ability]" (CR 702.186b's ∞, for one):
    a triggered ability that exists only under a condition cannot trigger
    while the condition is false. Carried as the ability's static condition,
    which is ALWAYS for every ordinary triggered ability."""
    condition = ability.static_condition
    if condition.is_always:
        return True
    from ..kernel.conditions import holds

    return holds(game, condition, source=obj.id, controller=obj.controller)


def _right_phase(trigger: TriggerCondition, event: Event) -> bool:
    """CR 500.6: a phase or step trigger fires as *its* phase or step begins."""
    if event.kind is EventKind.PHASE_BEGAN and trigger.phases:
        return event.amount in trigger.phases
    if event.kind is EventKind.STEP_BEGAN and trigger.steps:
        return event.amount in trigger.steps
    return True


def check_state_triggers(game: Game) -> None:
    """Fire state triggers whose condition has just become true (CR 603.8).

    A state trigger watches a *condition*, not an event: "when you control no
    creatures", "when a player has 0 or less life". CR 603.8 is the subtle
    part - it triggers once when the condition becomes true and does not
    trigger again until the condition has been false in between. Without that,
    a permanently-true condition would put an ability on the stack every single
    time priority came round.
    """
    from ..kernel.conditions import holds
    from .abilities import AbilityKind

    armed = game.armed_state_triggers
    still_true: set[tuple[int, int]] = set()

    for obj in _trigger_sources(game):
        chars = game.printed_characteristics(obj)
        for index, ability in enumerate(chars.abilities):
            if ability.kind is not AbilityKind.TRIGGERED or ability.unparsed:
                continue
            trigger = ability.trigger
            if trigger is None or not trigger.is_state_trigger:
                continue
            if obj.zone not in trigger.functions_in:
                continue

            key = (obj.id, index)
            if not holds(
                game, trigger.intervening_if, source=obj.id, controller=obj.controller
            ):
                continue

            still_true.add(key)
            if key in armed:
                continue  # Already triggered and the condition never went false.
            game.pending_triggers.append(
                PendingTrigger(
                    obj.id,
                    ability,
                    Event(EventKind.ABILITY_TRIGGERED, object_id=obj.id),
                    obj.controller,
                )
            )

    game.armed_state_triggers = still_true


def _watchers(game: Game) -> dict:
    """Which abilities could trigger on which event kind, indexed.

    Rebuilt when the epoch moves, which is the same signal the characteristics
    cache uses; ``_watched_population`` guards against an object arriving in a
    watched zone without one, because a stale index here would stop abilities
    firing silently rather than fail where anyone could see it.
    """
    population = _watched_population(game)
    cached = getattr(game, "_trigger_index", None)
    if cached is not None and game._trigger_index_key == (game.epoch, population):
        return cached

    index: dict = {}
    for obj in _trigger_sources(game):
        for ability in game.printed_characteristics(obj).abilities:
            if ability.kind is not AbilityKind.TRIGGERED or ability.unparsed:
                continue
            trigger = ability.trigger
            if trigger is None:
                continue
            for kind in _kinds_watched(trigger):
                index.setdefault(kind, []).append((obj.id, ability))

    game._trigger_index = index
    game._trigger_index_key = (game.epoch, population)
    return index


def _kinds_watched(trigger) -> frozenset:
    """Every event kind a trigger could fire on, alternatives included."""
    if not trigger.alternatives:
        return trigger.event_kinds
    kinds: set = set(trigger.event_kinds)
    for alternative in trigger.alternatives:
        kinds |= _kinds_watched(alternative)
    return frozenset(kinds)


def _watched_population(game: Game) -> int:
    """How many objects sit in the zones the index covers."""
    total = 0
    for zone in _WATCHED_SHARED_ZONES:
        total += len(game.zone_list(zone))
    for player in game.players:
        for zone in _WATCHED_PLAYER_ZONES:
            total += len(player.zone(zone))
    return total


def _trigger_sources(game: Game) -> Iterator[GameObject]:
    for zone in _WATCHED_SHARED_ZONES:
        for object_id in list(game.zone_list(zone)):
            obj = game.objects.get(object_id)
            if obj is not None and not obj.phased_out:
                yield obj
    for player in game.players:
        for zone in _WATCHED_PLAYER_ZONES:
            for object_id in list(player.zone(zone)):
                obj = game.objects.get(object_id)
                if obj is not None:
                    yield obj


def _subject_of(game: Game, event: Event) -> GameObject | None:
    """The object an event is about, using last-known information where required.

    CR 603.6e and 608.2g: a leave-the-battlefield trigger looks at the game as
    it was *before* the event. Asking "was it a creature you controlled?" of the
    card now sitting in the graveyard gets the wrong answer - it is no longer a
    creature, and no longer controlled by anyone - so the pre-move object is
    used instead. It is kept alive for exactly this purpose, and carries the
    zone it left from.
    """
    # CR 603.10: not only leaves-the-battlefield. Phasing out, becoming
    # unattached and losing control all look back in time, and each needs the
    # object as it was rather than as it now is.
    if event.looks_back_in_time and event.data:
        previous = game.objects.get(event.data[0])
        if previous is not None:
            return previous
    return game.objects.get(event.object_id)


def condition_met(
    game: Game, source: GameObject, trigger: TriggerCondition, event: Event
) -> bool:
    """Whether this trigger fires for this event."""
    if trigger.alternatives:
        # A disjunction whose halves are about different subjects. Each half
        # is a complete condition, so this is one recursion rather than a
        # union of constraints that would match combinations of them that no
        # half describes.
        return any(
            condition_met(game, source, alternative, event)
            for alternative in trigger.alternatives
        )

    if event.kind not in trigger.event_kinds:
        return False
    if not _right_phase(trigger, event):
        return False
    if trigger.to_player and event.object_id != NO_OBJECT:
        # Damage to a permanent carries the permanent's id; damage to a player
        # carries none (CR 120.3).
        return False

    # A counter trigger that names a kind fires for that kind alone: the
    # event carries it in ``data``.
    if trigger.counter_kind and (
        not event.data or event.data[0] != trigger.counter_kind
    ):
        return False

    from ..kernel.matching import matches, resolve_players

    if trigger.subject is not None:
        subject = _subject_of(game, event)
        if subject is None:
            return False
        if not matches(
            game,
            subject,
            trigger.subject,
            source=source.id,
            controller=source.controller,
            allow_stale=True,
        ):
            return False

    if trigger.source is not None:
        cause = game.objects.get(event.source)
        if cause is None:
            return False
        if not matches(
            game, cause, trigger.source, source=source.id, controller=source.controller
        ):
            return False

    if trigger.players is not None:
        if event.player == NO_PLAYER:
            return False
        allowed = resolve_players(game, trigger.players, controller=source.controller)
        if event.player not in allowed:
            return False

    # CR 714.2b: a chapter ability triggers when the number of lore counters
    # "was less than N and became at least N". Adding two counters at once
    # therefore fires chapters II and III together, and adding a counter to a
    # Saga already past that chapter fires nothing.
    if trigger.chapter:
        now = source.counter_count("lore")
        before = now - max(1, event.amount)
        if not (before < trigger.chapter <= now):
            return False

    # CR 309.4c: a room ability triggers when its owner's venture marker
    # moves into that room of that dungeon - not a room of the same number on
    # some other dungeon card.
    if trigger.room and (
        event.object_id != source.id or event.amount != trigger.room
    ):
        return False

    if trigger.ordinal and not _is_nth_this_turn(game, trigger, event):
        return False

    # CR 603.4: the intervening-if clause is checked here, and again on
    # resolution. Failing either time means the ability does nothing.
    return check_intervening_if(game, source, trigger)


def _is_nth_this_turn(game: Game, trigger: TriggerCondition, event: Event) -> bool:
    """Whether this is the nth such event for this player this turn.

    The tally is taken *after* the event is recorded, so the event being
    tested is already counted - the second spell is the one for which the
    running total reads two.
    """
    total = game.turn_history.get(
        (int(event.kind), int(event.player), "count"), 0
    )
    return total == trigger.ordinal


def check_intervening_if(game: Game, source: GameObject, trigger: TriggerCondition) -> bool:
    """Evaluate an intervening-if clause (CR 603.4)."""
    if trigger.intervening_if.is_always:
        return True
    from ..kernel.conditions import holds

    return holds(
        game, trigger.intervening_if, source=source.id, controller=source.controller
    )


def is_triggered_mana_ability(game: Game, source_id: int, ability: Ability) -> bool:
    """CR 605.1b: a triggered ability is a mana ability if it triggers off
    activating a mana ability, could add mana, and doesn't target.

    It matters because such an ability does *not* use the stack (CR 605.3b) -
    so it resolves inside cost payment, where the stack is not available. An
    engine that put it on the stack would deadlock every card that ramps off
    tapping a land.
    """
    if ability.trigger is None:
        return False
    if EventKind.MANA_ADDED not in ability.trigger.event_kinds:
        return False
    if ability.is_targeted:
        return False
    from .effects import EffectKind

    return any(
        node.kind is EffectKind.ADD_MANA
        for effect in ability.effects
        for node in effect.walk()
    )


def resolve_mana_triggers(game: Game) -> int:
    """Resolve pending triggered mana abilities immediately (CR 605.3b).

    Called wherever ordinary triggers would go on the stack. These never get
    there: they resolve where they stand, which is what lets them fire during
    the mana-ability window of casting a spell.
    """
    if not game.pending_triggers:
        return 0

    from .resolve import Resolution, execute

    remaining = []
    resolved = 0
    for pending in game.pending_triggers:
        if not is_triggered_mana_ability(game, pending.source, pending.ability):
            remaining.append(pending)
            continue
        controller = _controller_of(game, pending)
        if controller == NO_PLAYER:
            # CR 800.4a again: its controller has left, so it is gone. Dropped
            # rather than resolved, because mana added for nobody goes into a
            # pool that no player owns.
            resolved += 1
            continue
        # CR 603.3c: a modal ability's modes are chosen as it would go on the
        # stack. This one never gets there (CR 605.4), so they are chosen here
        # and carried on the resolution - there is no stack object to hold them.
        from .cr601_casting import choose_modes

        execute(
            Resolution(
                game=game,
                source=pending.source,
                controller=controller,
                chosen_modes=choose_modes(
                    game,
                    game.objects.get(pending.source),
                    pending.ability.effects,
                    controller,
                    source_id=pending.source,
                ),
            ),
            pending.ability.effects,
        )
        resolved += 1

    game.pending_triggers = remaining
    return resolved


def _controller_of(game: Game, pending: PendingTrigger) -> PlayerId:
    """Who controls this waiting ability, now.

    The controller captured when it triggered, unless that player has since
    left the game - in which case nobody does, and CR 800.4a says the ability
    ceases to exist rather than resolving under no one.

    The *source* being gone is not this case and never was: a dies-trigger
    outliving its creature is the ordinary way triggers work (CR 603.3d).
    """
    controller = pending.controller
    if not 0 <= controller < len(game.players):
        return NO_PLAYER
    return NO_PLAYER if game.players[controller].has_lost else controller


def put_triggers_on_stack(game: Game) -> int:
    """Move noted triggers onto the stack (CR 603.3b).

    Called whenever a player would receive priority, before they actually get
    it. Returns how many were put on the stack, because a player only receives
    priority after this settles.
    """
    if not game.pending_triggers:
        return 0

    from ..kernel.gameobject import ObjectKind

    # CR 605.3b: triggered mana abilities never use the stack. They resolve
    # first and are gone before anything else is put on it.
    resolve_mana_triggers(game)

    pending = game.pending_triggers
    game.pending_triggers = []
    count = 0

    for entry in pending:
        ability = entry.ability
        controller = _controller_of(game, entry)
        if controller == NO_PLAYER:
            # CR 800.4a: an ability controlled by a player who has left the
            # game ceases to exist. It is the only case left where nobody
            # controls this, and putting it on the stack anyway is what used
            # to make tokens owned by nobody.
            game.log.record(
                game,
                f"{ability.text or 'A triggered ability'} ceases to exist: "
                "its controller has left the game (CR 800.4a)",
                kind="trigger",
            )
            continue

        stack_object = GameObject(
            id=game.ids.object_id(),
            kind=ObjectKind.ABILITY,
            owner=controller,
            controller=controller,
            zone=Zone.STACK,
            timestamp=game.ids.timestamp(),
            ability=ability,
            source=entry.source,
            trigger_event=entry.event,
        )
        # CR 603.3d: modes and targets are chosen now, as the ability is put
        # on the stack - not when it triggered and not when it resolves. An
        # ability that finds no legal target is still put on the stack; it is
        # countered on resolution by CR 608.2b, and something may yet change
        # in between.
        #
        # CR 603.3c: a mode is different. If the ability is modal and no mode
        # can be chosen, it is removed from the stack - so it never goes on
        # it - and nothing about the game state can change that later.
        stack_object.chosen_modes = _choose_trigger_modes(game, stack_object, controller)
        if not stack_object.chosen_modes and _is_modal(ability):
            game.log.record(
                game,
                f"{ability.text or 'A triggered ability'} is removed from the stack: "
                "no mode can be chosen (CR 603.3c)",
                kind="trigger",
                player=controller,
            )
            continue
        stack_object.targets = _choose_trigger_targets(game, stack_object, controller)

        game.objects[stack_object.id] = stack_object
        game.stack.append(stack_object.id)
        game.log.record(
            game,
            f"Trigger goes on the stack: {ability}",
            kind="trigger",
            player=controller,
        )
        count += 1

    return count


def _is_modal(ability: Ability) -> bool:
    """Whether this ability has modes to choose at all (CR 700.2)."""
    from .cr601_casting import modal_effect

    return modal_effect(ability.effects) is not None


def _choose_trigger_modes(
    game: Game, stack_object: GameObject, controller: PlayerId
) -> tuple[int, ...]:
    """Ask this ability's controller to choose its mode(s) (CR 603.3c).

    The same choice a spell makes at CR 601.2b, made by the same machinery -
    there is one modal rule, and a triggered ability differs only in when it
    is asked and in what happens when nothing can be chosen.
    """
    from .cr601_casting import choose_modes

    ability = stack_object.ability
    if ability is None or controller == NO_PLAYER:
        return ()
    # The source may be gone - a dies-trigger outlives its creature - and a
    # mode's legality is then judged by its filters alone.
    return choose_modes(
        game,
        game.objects.get(stack_object.source),
        ability.effects,
        controller,
        source_id=stack_object.source,
    )


def _targeted_effects(ability: Ability, chosen_modes: tuple[int, ...] = ()) -> list:
    """The targeted effect nodes of an ability, in announcement order.

    Every targeted node, player-only ones included, because this list is
    lined up slot for slot against the one the resolver and the fizzle check
    walk. Leaving out "target opponent" - which has a player scope and no
    object filter - chose no target for it and shifted every later effect's
    targets onto the wrong effect.
    """
    from .cr601_casting import targeted_nodes

    # CR 700.2c: only a chosen mode's targets are chosen.
    return targeted_nodes(ability.effects, chosen_modes or None)


def _choose_trigger_targets(
    game: Game, stack_object: GameObject, controller: PlayerId
) -> tuple:
    """Ask this ability's controller to pick targets (CR 603.3d).

    Returns one tuple per targeted effect, in the order the effects appear, so
    the resolver can line them up the same way it does for a spell.
    """
    ability = stack_object.ability
    if ability is None or controller == NO_PLAYER:
        return ()
    effects = _targeted_effects(ability, stack_object.chosen_modes)
    if not effects:
        return ()

    from ..kernel.ids import player_target
    from ..kernel.matching import find
    from .cr601_casting import _candidates_for, _targetable_players

    source = game.objects.get(stack_object.source)

    def candidates_for(effect) -> list[int]:
        # The same candidates a spell's targets are chosen from, players
        # included (CR 115.4) - there is one targeting rule, not two.
        if source is not None:
            return _candidates_for(game, source, effect, controller)
        # The source is gone and nothing kept it: no protection to check
        # against it, so the filters alone decide.
        if effect.targets is None:
            return [
                player_target(pid)
                for pid in _targetable_players(game, effect.players, controller)
            ]
        found = [
            obj.id
            for obj in find(
                game, effect.targets, source=stack_object.source, controller=controller
            )
        ]
        if effect.targets.includes_players:
            found.extend(player_target(player.id) for player in game.living_players)
        return found

    candidates = [candidates_for(effect) for effect in effects]
    agent = game.agent_for(controller)
    chooser = getattr(agent, "choose_targets", None)
    if chooser is None:
        # No agent, or an agent that predates this hook. CR 601.2c still
        # requires a legal target to be chosen where one exists, so the
        # engine makes the deterministic choice itself rather than letting
        # the ability quietly fizzle.
        chosen = tuple((group[0],) if group else () for group in candidates)
    else:
        chosen = chooser(game, controller, stack_object.source, candidates)

    # The agent is not trusted to return something legal: an illegal choice is
    # dropped rather than allowed through, and the ability fizzles later if
    # nothing is left (CR 608.2b).
    cleaned: list[tuple] = []
    for index, effect in enumerate(effects):
        legal = set(candidates[index]) if index < len(candidates) else set()
        picked = tuple(chosen[index]) if index < len(chosen) else ()
        cleaned.append(tuple(object_id for object_id in picked if object_id in legal))
    return tuple(cleaned)
