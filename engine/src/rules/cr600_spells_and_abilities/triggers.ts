/**
 * Triggered abilities (CR 603). Ported from
 * mtgfish/rules/cr600_spells_and_abilities/cr603_triggers.py.
 *
 * A triggered ability does not go on the stack when it triggers. It triggers,
 * is noted (`game.pendingTriggers`), and then goes on the stack the next time
 * a player would receive priority (CR 603.3b) - which is why a creature can
 * die, its dies-trigger can be noted, and the trigger still resolves though
 * the creature is long gone.
 *
 * The intervening-if clause (CR 603.4) is checked twice: once when the
 * ability would trigger and again as it resolves.
 */

import type { Ability, Condition, Effect, ObjectFilter, TriggerCondition } from "../../ir";
import { Zone } from "../../kernel/enums";
import { event, EventKind, looksBackInTime, withAmount, type GameEvent } from "../../kernel/events";
import { agentFor, apnapOrder, livingPlayers, logRecord, turnHistoryCountKey, zoneList, type Game } from "../../kernel/game";
import { counterCount, isLive, newGameObject, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { allocateObjectId, allocateTimestamp, NO_OBJECT, NO_PLAYER, playerTarget, type ObjectId, type PlayerId } from "../../kernel/ids";
import { hasKeyword } from "../cr200_parts_of_a_card/characteristics";
import type { Agent } from "../cr100_game_concepts/priority";
import { emit } from "../emit";
import { flag, ir, irEqual, list, num, numSet } from "../ir_read";
import { characteristicsOf, printedCharacteristics } from "./layers";
import { chooseModes, modalEffect } from "./modes";
import { execute, newResolution } from "./resolve";
import { includesPlayers, targetsAPlayer, walk } from "./effect_props";
import { abilityIsTargeted, announceTargets, candidatesFor, settleTargetChoices, targetablePlayers, targetBounds, targetedNodes } from "./targeting";

const TRIGGERED: number = ir("AbilityKind.TRIGGERED");
const STATIC: number = ir("AbilityKind.STATIC");
const ALWAYS: number = ir("ConditionKind.ALWAYS");
const EXTRA_TRIGGER: number = ir("EffectKind.EXTRA_TRIGGER");
const ADD_MANA: number = ir("EffectKind.ADD_MANA");

/**
 * An ability that has triggered and is waiting for the stack (CR 603.3).
 *
 * `controller` is captured when the ability triggers, not read off the
 * source when it goes on the stack (CR 603.3d): by then the source may be
 * gone - a token that ceased to exist, or a permanent whose owner left the
 * game (CR 800.4a).
 */
export interface PendingTrigger {
  source: ObjectId;
  ability: Ability;
  event: GameEvent;
  controller: PlayerId;
  /** `game.eventBatch` when it triggered - which "one or more" trigger a later event of the batch folds into (CR 603.2c). */
  batch: number;
}

/** Python `PendingTrigger(source, ability, event, controller, batch=-1)`. */
export function pendingTrigger(source: ObjectId, ability: Ability, e: GameEvent, controller: PlayerId, batch = -1): PendingTrigger {
  return { source, ability, event: e, controller, batch };
}

/**
 * A trigger created by a resolving spell or ability (CR 603.7), from
 * abilities.py `DelayedTrigger`. It fires at most once unless `repeating`.
 */
export interface DelayedTrigger {
  trigger: TriggerCondition;
  effects: readonly Effect[];
  controller: PlayerId;
  source: ObjectId;
  repeating: boolean;
  expired: boolean;
  /** Objects the effect referred to when it was created (CR 603.7c). */
  remembered: readonly ObjectId[];
}

export function delayedTrigger(
  trigger: TriggerCondition,
  effects: readonly Effect[],
  controller: PlayerId,
  source: ObjectId,
  options: { repeating?: boolean; remembered?: readonly ObjectId[] } = {},
): DelayedTrigger {
  return {
    trigger,
    effects,
    controller,
    source,
    repeating: options.repeating ?? false,
    expired: false,
    remembered: (options.remembered ?? []).slice(),
  };
}

const pendingOf = (game: Game): PendingTrigger[] => game.pendingTriggers as PendingTrigger[];
const delayedOf = (game: Game): DelayedTrigger[] => game.delayedTriggers as DelayedTrigger[];

/**
 * Zones scanned for triggered abilities. The library is deliberately
 * excluded: no printed triggered ability functions from a library.
 */
const WATCHED_SHARED_ZONES: readonly Zone[] = [Zone.BATTLEFIELD, Zone.COMMAND, Zone.STACK, Zone.EXILE];
const WATCHED_PLAYER_ZONES: readonly Zone[] = [Zone.GRAVEYARD, Zone.HAND];

type Found = [GameObject, Ability][];

const isAlways = (c: Condition | null | undefined): boolean => c == null || num(c.kind) === ALWAYS;
const eventKinds = (t: TriggerCondition): Set<number> => numSet(t.event_kinds);
const functionsIn = (t: TriggerCondition): Set<number> => numSet(t.functions_in, [Zone.BATTLEFIELD]);

/** A canonical string for an ability, where Python keyed sets by its value. */
function abilityKey(ability: Ability): string {
  return stableJson(ability);
}
function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record)
    .filter((k) => record[k] !== undefined)
    .sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableJson(record[k])}`).join(",")}}`;
}

/**
 * Note every ability that triggers on `e` (CR 603.2). Appended to
 * `game.pendingTriggers` in APNAP order (CR 101.4, 603.3b).
 */
export function collectTriggers(game: Game, e: GameEvent): void {
  // A loop shortcut applies many cycles at once; a per-cycle trigger is
  // already part of what is being scaled.
  if (game.suppressTriggers) return;

  const found: Found = [];
  collectDelayed(game, e, found);

  for (const [objectId, ability] of watchers(game).get(e.kind) ?? []) {
    const obj = game.objects.get(objectId);
    if (obj === undefined || obj.phasedOut) continue;
    const trigger = ability.trigger!;
    if (!functionsIn(trigger).has(obj.zone) && !castTriggerOnTheStack(obj, trigger)) continue;
    if (!hasTheAbilityNow(game, obj, ability)) continue;
    if (!conditionMet(game, obj, trigger, e)) continue;
    found.push([obj, ability]);
  }

  if (looksBackInTime(e)) {
    collectDepartedToken(game, e, found);
    collectDepartedCard(game, e, found);
    collectDepartedTogether(game, e, found);
  }

  if (found.length === 0) return;

  // APNAP: the active player's triggers go on the stack first (CR 603.3b).
  const order = new Map<PlayerId, number>();
  apnapOrder(game).forEach((p, index) => order.set(p, index));
  // A stable sort, as Python's.
  found.sort(([a], [b]) => (order.get(a.controller) ?? 99) - (order.get(b.controller) ?? 99) || a.timestamp - b.timestamp);

  const pending = pendingOf(game);
  for (const [obj, ability] of found) {
    const trigger = ability.trigger ?? null;
    if (trigger !== null && flag(trigger.batched) && foldIntoBatch(game, obj, ability, e)) continue;
    if (trigger !== null && flag(trigger.once_each_turn)) {
      // "This ability triggers only once each turn" (CR 603.2).
      const key = `${obj.id}|${abilityKey(ability)}`;
      if (game.triggeredOnceThisTurn.has(key)) continue;
      game.triggeredOnceThisTurn.add(key);
    }
    const [source, controller] = sourceFor(game, obj, ability, e);
    let noted = e;
    if (trigger !== null && flag(trigger.batched)) {
      // "That many" counts the batch (CR 603.2c).
      noted = withAmount(e, Math.max(1, e.amount));
    }
    const entry = pendingTrigger(source, ability, noted, controller, game.eventBatch);
    pending.push(entry);
    // CR 603.2c: three counters placed at once are three occurrences.
    if (trigger !== null && flag(trigger.each_counter) && !flag(trigger.once_each_turn) && e.amount > 1) {
      for (let i = 0; i < e.amount - 1; i += 1) pending.push(entry);
    }
    // CR 603.2b: something may say this ability triggers an extra time.
    const extra = extraTriggers(game, obj, e);
    for (let i = 0; i < extra; i += 1) pending.push(entry);
  }
}

/**
 * The source and controller a noted trigger goes on the stack with. CR
 * 400.7e: an ability that triggered on its source leaving the battlefield
 * can find the object the source became in a public zone; CR 603.3a: the
 * controller is the one it had as it left.
 */
function sourceFor(game: Game, obj: GameObject, ability: Ability, e: GameEvent): [ObjectId, PlayerId] {
  if (isLive(obj) || e.data.length === 0 || e.data[0] !== obj.id) return [obj.id, obj.controller];
  const became = game.objects.get(obj.supersededBy);
  if (
    became === undefined ||
    became.id !== e.objectId ||
    !isLive(became) ||
    became.zone === Zone.HAND ||
    became.zone === Zone.LIBRARY ||
    ability.trigger == null ||
    !functionsIn(ability.trigger).has(became.zone)
  ) {
    return [obj.id, obj.controller];
  }
  return [became.id, obj.controller];
}

/** CR 113.6k: "when you cast this spell" functions on the stack. */
function castTriggerOnTheStack(obj: GameObject, trigger: TriggerCondition): boolean {
  return obj.zone === Zone.STACK && eventKinds(trigger).has(EventKind.CAST_SPELL) && trigger.subject != null && flag(trigger.subject.source_only);
}

/**
 * CR 603.2c: "whenever one or more creatures die" triggers once for
 * everything that happened at the same time; the pending trigger's amount
 * grows instead.
 */
function foldIntoBatch(game: Game, obj: GameObject, ability: Ability, e: GameEvent): boolean {
  let folded = false;
  const pending = pendingOf(game);
  const trigger = ability.trigger!;
  pending.forEach((p, index) => {
    if (
      p.batch === game.eventBatch &&
      p.source === obj.id &&
      irEqual(p.ability, ability) &&
      (p.event.player === e.player || !(flag(trigger.to_player) || trigger.players != null))
    ) {
      pending[index] = { ...p, event: withAmount(p.event, p.event.amount + Math.max(1, e.amount)) };
      folded = true;
    }
  });
  return folded;
}

/** A token's own "when this dies" (CR 603.10a): a token arrives nowhere (CR 111.7). */
function collectDepartedToken(game: Game, e: GameEvent, found: Found): void {
  const token = game.objects.get(e.objectId);
  if (token === undefined || token.kind !== ObjectKind.TOKEN || token.phasedOut) return;
  if (found.some(([obj]) => obj.id === token.id)) return;
  for (const ability of lastKnownAbilities(game, token)) {
    if (num(ability.kind) !== TRIGGERED || flag(ability.unparsed)) continue;
    const trigger = ability.trigger;
    if (trigger == null || !kindsWatched(trigger).has(e.kind)) continue;
    if (conditionMet(game, token, trigger, e)) found.push([token, ability]);
  }
}

/** A card's own leaves-the-battlefield abilities (CR 603.10a, 603.6c), from its pre-move object. */
function collectDepartedCard(game: Game, e: GameEvent, found: Found): void {
  if (e.data.length === 0) return;
  const departed = game.objects.get(e.data[0] as ObjectId);
  if (departed === undefined || departed.id === e.objectId) return;
  if (departed.zone !== Zone.BATTLEFIELD || departed.phasedOut) return;
  // CR 708.2: a face-down permanent has no abilities of its own.
  if (departed.faceDown) return;
  for (const ability of lastKnownAbilities(game, departed)) {
    if (num(ability.kind) !== TRIGGERED || flag(ability.unparsed)) continue;
    const trigger = ability.trigger;
    if (trigger == null || !kindsWatched(trigger).has(e.kind)) continue;
    if (!functionsIn(trigger).has(Zone.BATTLEFIELD)) continue;
    if (found.some(([obj, other]) => obj.id === e.objectId && irEqual(other, ability))) continue;
    if (conditionMet(game, departed, trigger, e)) found.push([departed, ability]);
  }
}

/** Permanents that left at the same time as this one (CR 603.10a). */
function collectDepartedTogether(game: Game, e: GameEvent, found: Found): void {
  if (game.departedBatch !== game.eventBatch) return;
  const own = e.data.length > 0 ? e.data[0] : e.objectId;
  for (const departedId of game.departedInBatch) {
    if (departedId === own) continue;
    const departed = game.objects.get(departedId);
    if (departed === undefined || departed.phasedOut || departed.faceDown) continue;
    if (found.some(([obj]) => obj.id === departedId)) continue;
    for (const ability of lastKnownAbilities(game, departed)) {
      if (num(ability.kind) !== TRIGGERED || flag(ability.unparsed)) continue;
      const trigger = ability.trigger;
      if (trigger == null || !kindsWatched(trigger).has(e.kind)) continue;
      if (!functionsIn(trigger).has(Zone.BATTLEFIELD)) continue;
      if (found.some(([obj, other]) => obj.id === departed.supersededBy && irEqual(other, ability))) continue;
      if (conditionMet(game, departed, trigger, e)) found.push([departed, ability]);
    }
  }
}

/** How many additional times this object's abilities trigger (CR 603.2b). */
function extraTriggers(game: Game, obj: GameObject, e: GameEvent): number {
  const hooks = matchHooks();
  let extra = 0;
  for (const sourceId of game.battlefield) {
    const source = game.objects.get(sourceId);
    if (source === undefined || source.phasedOut) continue;
    for (const ability of characteristicsOf(game, source).abilities) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      for (const effect of list(ability.effects)) {
        if (num(effect.kind) !== EXTRA_TRIGGER) continue;
        const context = { source: source.id, controller: source.controller, allowStale: true };
        if (effect.targets != null && !hooks.matches(game, obj, effect.targets, context)) continue;
        const cause = subjectOf(game, e);
        if (effect.trigger_cause != null && (cause === null || !hooks.matches(game, cause, effect.trigger_cause, context))) continue;
        extra += Math.max(1, num(effect.amount?.constant));
      }
    }
  }
  return extra;
}

/**
 * Fire delayed triggered abilities waiting on this event (CR 603.7). Once,
 * the next time the event happens (CR 603.7b), unless repeating.
 */
function collectDelayed(game: Game, e: GameEvent, found: Found): void {
  if (game.delayedTriggers.length === 0) return;
  const hooks = matchHooks();
  for (const delayed of delayedOf(game).slice()) {
    if (delayed.expired) continue;
    const trigger = delayed.trigger;
    if (!eventKinds(trigger).has(e.kind)) continue;
    if (!rightPhase(trigger, e)) continue;

    let source = game.objects.get(delayed.source);
    if (source === undefined) {
      // The ability exists independently of whatever created it: a bare
      // stand-in gives it a controller.
      source = newGameObject(allocateObjectId(game.ids), {
        kind: ObjectKind.ABILITY,
        owner: delayed.controller,
        controller: delayed.controller,
        baseController: delayed.controller,
        zone: Zone.COMMAND,
      });
      game.objects.set(source.id, source);
    }

    let remembered: readonly ObjectId[] = delayed.remembered;
    if (trigger.subject != null) {
      // CR 603.7: "when the permanent that exiled it leaves the battlefield"
      // is about one permanent; matched on last known information (CR 603.6e).
      const subject = subjectOf(game, e);
      if (
        subject === null ||
        !hooks.matches(game, subject, bindRemembered(trigger.subject, delayed.remembered), {
          controller: delayed.controller,
          allowStale: true,
        })
      ) {
        continue;
      }
      // CR 603.7c: "when that land dies, return it" - "it" is the one the event was about.
      if (flag(trigger.subject.remembered)) remembered = [subject.id];
    }

    if (trigger.players != null && e.player !== NO_PLAYER) {
      if (!hooks.resolvePlayers(game, trigger.players, { controller: delayed.controller }).includes(e.player)) continue;
    }

    const ability: Ability = {
      $: "Ability",
      kind: TRIGGERED,
      effects: delayed.effects.slice(),
      trigger,
      text: trigger.text || "delayed trigger",
      remembered: remembered.slice(),
    };
    found.push([source, ability]);
    if (!delayed.repeating) delayed.expired = true;
  }
  game.delayedTriggers = delayedOf(game).filter((d) => !d.expired);
}

/** A delayed trigger's "that land" pinned to the objects it was created about (CR 603.7c). */
function bindRemembered(spec: ObjectFilter, remembered: readonly ObjectId[]): ObjectFilter {
  if (!flag(spec.remembered)) return spec;
  return { ...spec, remembered: false, specific: remembered.length > 0 ? remembered.slice() : [NO_OBJECT] };
}

/** "As long as [condition], it has [ability]": carried as the ability's static condition. */
function hasTheAbilityNow(game: Game, obj: GameObject, ability: Ability): boolean {
  const condition = ability.static_condition;
  if (isAlways(condition)) return true;
  return matchHooks().holds(game, condition!, { source: obj.id, controller: obj.controller });
}

/** CR 500.6: a phase or step trigger fires as its phase or step begins. */
function rightPhase(trigger: TriggerCondition, e: GameEvent): boolean {
  if (e.kind === EventKind.PHASE_BEGAN) {
    const phases = numSet(trigger.phases);
    if (phases.size > 0) return phases.has(e.amount);
  }
  if (e.kind === EventKind.STEP_BEGAN) {
    const steps = numSet(trigger.steps);
    if (steps.size > 0) return steps.has(e.amount);
  }
  return true;
}

/**
 * Fire state triggers whose condition has just become true (CR 603.8): once
 * when it becomes true, and not again until it has been false in between.
 */
export function checkStateTriggers(game: Game): void {
  const hooks = matchHooks();
  const armed = game.armedStateTriggers;
  const stillTrue = new Set<string>();
  for (const obj of triggerSources(game)) {
    triggerAbilities(game, obj).forEach((ability, index) => {
      if (num(ability.kind) !== TRIGGERED || flag(ability.unparsed)) return;
      const trigger = ability.trigger;
      if (trigger == null || !flag(trigger.is_state_trigger)) return;
      if (!functionsIn(trigger).has(obj.zone)) return;
      const key = `${obj.id}|${index}`;
      const condition = trigger.intervening_if ?? { $: "Condition" };
      if (!hooks.holds(game, condition, { source: obj.id, controller: obj.controller })) return;
      stillTrue.add(key);
      if (armed.has(key)) return; // Already triggered and never went false.
      pendingOf(game).push(pendingTrigger(obj.id, ability, event(EventKind.ABILITY_TRIGGERED, { objectId: obj.id }), obj.controller));
    });
  }
  game.armedStateTriggers = stillTrue;
}

type Index = Map<number, [ObjectId, Ability][]>;

/**
 * Which abilities could trigger on which event kind, indexed; rebuilt when
 * the epoch or the watched population moves.
 */
function watchers(game: Game): Index {
  const key = `${game.epoch}|${watchedPopulation(game)}`;
  if (game.triggerIndex !== null && game.triggerIndexKey === key) return game.triggerIndex as Index;
  const index: Index = new Map();
  for (const obj of triggerSources(game)) {
    for (const ability of triggerAbilities(game, obj)) {
      if (num(ability.kind) !== TRIGGERED || flag(ability.unparsed)) continue;
      const trigger = ability.trigger;
      if (trigger == null) continue;
      for (const kind of kindsWatched(trigger)) {
        let entries = index.get(kind);
        if (entries === undefined) index.set(kind, (entries = []));
        entries.push([obj.id, ability]);
      }
    }
  }
  game.triggerIndex = index;
  game.triggerIndexKey = key;
  return index;
}

/**
 * The abilities that may trigger for this object: as the layers leave it on
 * the battlefield, on the stack and face down (CR 613.1f, 601.2a, 708.2);
 * printed ones elsewhere.
 */
function triggerAbilities(game: Game, obj: GameObject): readonly Ability[] {
  if (obj.faceDown || obj.zone === Zone.BATTLEFIELD || obj.zone === Zone.STACK) return characteristicsOf(game, obj).abilities;
  return printedCharacteristics(game, obj).abilities;
}

/** CR 603.10a: the abilities a permanent had immediately before it left. */
function lastKnownAbilities(game: Game, departed: GameObject): readonly Ability[] {
  if (departed.lastKnownCharacteristics !== null) return departed.lastKnownCharacteristics.abilities;
  return printedCharacteristics(game, departed).abilities;
}

/** Every event kind a trigger could fire on, alternatives included. */
function kindsWatched(trigger: TriggerCondition): Set<number> {
  const kinds = eventKinds(trigger);
  for (const alternative of list(trigger.alternatives)) for (const k of kindsWatched(alternative)) kinds.add(k);
  return kinds;
}

function watchedPopulation(game: Game): number {
  let total = 0;
  for (const zone of WATCHED_SHARED_ZONES) total += zoneList(game, zone).length;
  for (const p of game.players) for (const zone of WATCHED_PLAYER_ZONES) total += zoneList(game, zone, p.id).length;
  return total;
}

function* triggerSources(game: Game): Generator<GameObject> {
  for (const zone of WATCHED_SHARED_ZONES) {
    for (const id of zoneList(game, zone).slice()) {
      const obj = game.objects.get(id);
      if (obj !== undefined && !obj.phasedOut) yield obj;
    }
  }
  for (const p of game.players) {
    for (const zone of WATCHED_PLAYER_ZONES) {
      for (const id of zoneList(game, zone, p.id).slice()) {
        const obj = game.objects.get(id);
        if (obj !== undefined) yield obj;
      }
    }
  }
}

/**
 * The object an event is about, on last-known information where the event
 * looks back in time (CR 603.6e, 603.10, 608.2g). Python `_subject_of`.
 */
export function subjectOf(game: Game, e: GameEvent): GameObject | null {
  if (looksBackInTime(e) && e.data.length > 0) {
    const previous = game.objects.get(e.data[0] as ObjectId);
    if (previous !== undefined) return previous;
  }
  return game.objects.get(e.objectId) ?? null;
}

/** Whether this trigger fires for this event. */
export function conditionMet(game: Game, source: GameObject, trigger: TriggerCondition, e: GameEvent): boolean {
  const alternatives = list(trigger.alternatives);
  if (alternatives.length > 0) {
    // A disjunction; an intervening-if written after it belongs to the whole (CR 603.4).
    return alternatives.some((alt) => conditionMet(game, source, alt, e)) && checkInterveningIf(game, source, trigger, e);
  }
  if (!eventKinds(trigger).has(e.kind)) return false;
  if (!rightPhase(trigger, e)) return false;
  const expend = num(trigger.expend);
  if (expend && e.amount !== expend) return false;
  // Damage to a permanent carries its id; damage to a player none (CR 120.3).
  if (flag(trigger.to_player) && e.objectId !== NO_OBJECT) return false;
  // CR 508.3d: "whenever [a player] attacks" - not on a declaration of none.
  if (e.kind === EventKind.ATTACKERS_DECLARED && e.amount <= 0) return false;
  const fromZones = numSet(trigger.from_zones);
  if (fromZones.size > 0 && (e.fromZone === null || !fromZones.has(e.fromZone))) return false;
  // A counter trigger that names a kind fires for that kind alone.
  const counterKind = trigger.counter_kind ?? "";
  if (counterKind && (e.data.length === 0 || e.data[0] !== counterKind)) return false;

  const hooks = matchHooks();
  if (trigger.subject != null) {
    const subject = subjectOf(game, e);
    if (subject === null) return false;
    if (!hooks.matches(game, subject, trigger.subject, { source: source.id, controller: source.controller, allowStale: true })) return false;
  }
  if (trigger.source != null) {
    const cause = game.objects.get(e.source);
    if (cause === undefined) return false;
    if (!hooks.matches(game, cause, trigger.source, { source: source.id, controller: source.controller })) return false;
  }
  if (trigger.players != null) {
    if (e.player === NO_PLAYER) return false;
    if (!hooks.resolvePlayers(game, trigger.players, { controller: source.controller, source: source.id }).includes(e.player)) return false;
  }

  // CR 714.2b: a chapter ability triggers when the lore count "was less than
  // N and became at least N".
  const chapter = num(trigger.chapter);
  if (chapter) {
    const now = counterCount(source, "lore");
    const before = now - Math.max(1, e.amount);
    if (!(before < chapter && chapter <= now)) return false;
    // CR 702.155a: read ahead skips the chapters below the number chosen.
    if (source.enteredBattlefieldTurn === game.turn && hasKeyword(characteristicsOf(game, source), "Read Ahead") && now !== chapter) {
      return false;
    }
  }

  // CR 309.4c: a room ability triggers for its own dungeon's room.
  const room = num(trigger.room);
  if (room && (e.objectId !== source.id || e.amount !== room)) return false;

  if (num(trigger.ordinal) && !isNthThisTurn(game, trigger, e)) return false;

  // CR 702.55a
  if (flag(trigger.during_own_resolution) && (e.data.length === 0 || e.data[0] !== game.resolvingSpell)) return false;

  // CR 603.4: checked here, and again on resolution.
  return checkInterveningIf(game, source, trigger, e);
}

/** Whether this is the nth such event for this player this turn (counted after recording). */
function isNthThisTurn(game: Game, trigger: TriggerCondition, e: GameEvent): boolean {
  const total = game.turnHistory.get(turnHistoryCountKey(e.kind, e.player)) ?? 0;
  return total === num(trigger.ordinal);
}

/**
 * Evaluate an intervening-if clause (CR 603.4). `e` is what "it" means in
 * "whenever a creature dies, if it was a Human".
 */
export function checkInterveningIf(game: Game, source: GameObject, trigger: TriggerCondition, e: GameEvent | null = null): boolean {
  if (isAlways(trigger.intervening_if)) return true;
  return matchHooks().holds(game, trigger.intervening_if!, { source: source.id, controller: source.controller, event: e });
}

/**
 * CR 605.1b: a triggered ability is a mana ability if it triggers off
 * activating a mana ability, could add mana, and doesn't target - and so
 * never uses the stack (CR 605.3b).
 */
export function isTriggeredManaAbility(_game: Game, _sourceId: ObjectId, ability: Ability): boolean {
  if (ability.trigger == null) return false;
  const kinds = eventKinds(ability.trigger);
  if (!kinds.has(EventKind.MANA_ADDED) && !kinds.has(EventKind.TAPPED_FOR_MANA)) return false;
  if (abilityIsTargeted(ability)) return false;
  for (const effect of list(ability.effects)) for (const node of walk(effect)) if (num(node.kind) === ADD_MANA) return true;
  return false;
}

/** Resolve pending triggered mana abilities immediately (CR 605.3b); how many. */
export function resolveManaTriggers(game: Game): number {
  if (game.pendingTriggers.length === 0) return 0;
  const remaining: PendingTrigger[] = [];
  let resolved = 0;
  for (const pending of pendingOf(game)) {
    if (!isTriggeredManaAbility(game, pending.source, pending.ability)) {
      remaining.push(pending);
      continue;
    }
    const controller = controllerOf(game, pending);
    if (controller === NO_PLAYER) {
      // CR 800.4a: its controller left; dropped rather than resolved.
      resolved += 1;
      continue;
    }
    // CR 603.3c: modes chosen here, with no stack object to hold them.
    const effects = list(pending.ability.effects);
    execute(
      newResolution(game, pending.source, controller, {
        triggerEvent: pending.event,
        chosenModes: chooseModes(game, game.objects.get(pending.source) ?? null, effects, controller, { sourceId: pending.source }),
      }),
      effects,
    );
    resolved += 1;
  }
  game.pendingTriggers = remaining;
  return resolved;
}

/** Who controls this waiting ability now: nobody once that player has left (CR 800.4a). */
function controllerOf(game: Game, pending: PendingTrigger): PlayerId {
  const controller = pending.controller;
  if (!(controller >= 0 && controller < game.players.length)) return NO_PLAYER;
  return game.players[controller]!.hasLost ? NO_PLAYER : controller;
}

/** Text for a log line, as Python's `str(ability)` where the ability has text. */
function abilityLabel(ability: Ability): string {
  return ability.text || "A triggered ability";
}

/**
 * Move noted triggers onto the stack (CR 603.3b), called whenever a player
 * would receive priority. Returns how many went on the stack.
 */
export function putTriggersOnStack(game: Game): number {
  if (game.pendingTriggers.length === 0) return 0;
  // CR 605.3b: triggered mana abilities resolve first and never use the stack.
  resolveManaTriggers(game);

  const pending = pendingOf(game);
  game.pendingTriggers = [];
  let count = 0;
  // What happens from here on happens after these triggered (CR 603.2c).
  game.eventBatch += 1;

  for (const entry of pending) {
    const ability = entry.ability;
    const controller = controllerOf(game, entry);
    if (controller === NO_PLAYER) {
      logRecord(game, `${abilityLabel(ability)} ceases to exist: its controller has left the game (CR 800.4a)`, { kind: "trigger" });
      continue;
    }
    const stackObject = newGameObject(allocateObjectId(game.ids), {
      kind: ObjectKind.ABILITY,
      owner: controller,
      controller,
      baseController: controller,
      zone: Zone.STACK,
      timestamp: allocateTimestamp(game.ids),
      ability,
      source: entry.source,
      triggerEvent: entry.event,
    });
    // CR 603.3d: modes and targets are chosen as it goes on the stack. CR
    // 603.3c: a modal ability with no choosable mode never goes on it.
    stackObject.chosenModes = chooseTriggerModes(game, stackObject, controller);
    if (stackObject.chosenModes.length === 0 && modalEffect(list(ability.effects)) !== null) {
      logRecord(game, `${abilityLabel(ability)} is removed from the stack: no mode can be chosen (CR 603.3c)`, {
        kind: "trigger",
        player: controller,
      });
      continue;
    }
    stackObject.targets = chooseTriggerTargets(game, stackObject, controller);

    game.objects.set(stackObject.id, stackObject);
    game.stack.push(stackObject.id);
    emit(game, event(EventKind.PUT_ON_STACK, { objectId: stackObject.id, player: controller, source: entry.source }));
    // Ward (CR 702.21a) watches a permanent becoming a target.
    announceTargets(game, stackObject, controller);
    logRecord(game, `Trigger goes on the stack: ${ability.text ?? ""}`, { kind: "trigger", player: controller });
    count += 1;
  }
  return count;
}

/** CR 603.3c: the same modal choice a spell makes at CR 601.2b. */
function chooseTriggerModes(game: Game, stackObject: GameObject, controller: PlayerId): number[] {
  const ability = stackObject.ability;
  if (ability === null || controller === NO_PLAYER) return [];
  return chooseModes(game, game.objects.get(stackObject.source) ?? null, list(ability.effects), controller, { sourceId: stackObject.source });
}

/**
 * Ask this ability's controller to pick targets (CR 603.3d): one group per
 * targeted effect, in order, as for a spell.
 */
function chooseTriggerTargets(game: Game, stackObject: GameObject, controller: PlayerId): number[][] {
  const ability = stackObject.ability;
  if (ability === null || controller === NO_PLAYER) return [];
  // CR 700.2c: only a chosen mode's targets are chosen.
  const effects = targetedNodes(list(ability.effects), stackObject.chosenModes.length > 0 ? stackObject.chosenModes : null);
  if (effects.length === 0) return [];
  const source = game.objects.get(stackObject.source);
  const hooks = matchHooks();

  const candidatesOf = (effect: Effect): number[] => {
    if (source !== undefined) return candidatesFor(game, source, effect, controller);
    // The source is gone: the filters alone decide.
    if (targetsAPlayer(effect)) return targetablePlayers(game, effect.players ?? null, controller).map(playerTarget);
    const found = hooks.find(game, effect.targets!, { source: stackObject.source, controller }).map((o) => o.id);
    if (includesPlayers(effect.targets!)) for (const p of livingPlayers(game)) found.push(playerTarget(p.id));
    return found;
  };

  const candidates = effects.map(candidatesOf);
  const agent = agentFor(game, controller) as Partial<Agent> | null | undefined;
  const chooser = agent?.chooseTargets;
  // With no agent the engine makes the deterministic choice (CR 601.2c).
  const chosen =
    chooser === undefined
      ? candidates.map((group) => (group.length > 0 ? [group[0]!] : []))
      : chooser.call(agent, game, controller, stackObject.source, candidates);
  const bounds = effects.map((effect) => targetBounds(game, effect, controller, stackObject.xValue));
  return settleTargetChoices(effects, candidates, chosen, { fillRequired: chooser === undefined, bounds });
}

