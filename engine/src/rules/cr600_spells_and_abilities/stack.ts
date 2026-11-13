/**
 * The stack, and resolving what is on it (CR 405, 608). Ported from
 * mtgfish/rules/cr600_spells_and_abilities/cr608_stack.py.
 *
 * CR 608.2b: a spell or ability is countered on resolution only if *all*
 * its targets have become illegal; otherwise it does what it can.
 */

import type { Ability, Effect } from "../../ir";
import { CardType, Zone } from "../../kernel/enums";
import { event, EventKind, type GameEvent } from "../../kernel/events";
import { invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { addCounters, counterCount, isLive, newGameObject, objectText, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { allocateObjectId, allocateTimestamp, isPlayerTarget, targetPlayer, type ObjectId } from "../../kernel/ids";
import { popDepth, pushDepth } from "../../kernel/log";
import { attach, attachToPlayer, carryLinks } from "../cr100_game_concepts/actions";
import { hasType } from "../cr200_parts_of_a_card/characteristics";
import { isPermanentType } from "../cr200_parts_of_a_card/typeline";
import { CastMode, RESOLUTION_ZONE } from "../cr300_card_types/card_types";
import { enterAttacking } from "../cr500_turn_structure/combat";
import { onPermanentSpellResolved, resolutionDestination } from "../cr700_additional_rules/paid_costs";
import { emit } from "../emit";
import { ir, list, num } from "../ir_read";
import { moveObject } from "../zones";
import { withAnnouncedX } from "./casting";
import { characteristicsOf } from "./layers";
import { execute, newResolution, type Resolution } from "./resolve";
import { auraTargetEffect, targetedNodes } from "./targeting";
import { checkInterveningIf, subjectOf } from "./triggers";
import type { Characteristics } from "../cr200_parts_of_a_card/characteristics";

const TRIGGERED: number = ir("AbilityKind.TRIGGERED");
const SPELL: number = ir("AbilityKind.SPELL");
const ABILITY_TRIGGERED_EVENT = EventKind.ABILITY_TRIGGERED;

const nameOf = (game: Game, obj: GameObject): string => objectText(obj, characteristicsOf(game, obj).name);

/** The object that will resolve next, or null. */
export function top(game: Game): GameObject | null {
  const id = game.stack[game.stack.length - 1];
  return id === undefined ? null : (game.objects.get(id) ?? null);
}

/** Resolve the top of the stack (CR 608); whether anything resolved. */
export function resolveTop(game: Game): boolean {
  const obj = top(game);
  if (obj === null) return false;
  game.stack.pop();
  logRecord(game, `Resolving ${nameOf(game, obj)}`, { kind: "resolve", player: obj.controller });
  pushDepth(game.log);
  try {
    if (obj.kind === ObjectKind.ABILITY) resolveAbility(game, obj);
    else resolveSpell(game, obj);
  } finally {
    popDepth(game.log);
  }
  invalidateAllCharacteristics(game);
  return true;
}

// -- abilities ------------------------------------------------------------------------

function resolveAbility(game: Game, obj: GameObject): void {
  const ability = obj.ability;
  if (ability === null) {
    cease(game, obj);
    return;
  }
  const triggerEvent = obj.triggerEvent as GameEvent | null;
  // CR 603.4: an intervening "if" is checked again here.
  if (num(ability.kind) === TRIGGERED && ability.trigger != null) {
    const source = game.objects.get(obj.source);
    if (source !== undefined && !checkInterveningIf(game, source, ability.trigger, triggerEvent)) {
      logRecord(game, "Intervening 'if' no longer true; ability does nothing");
      cease(game, obj);
      return;
    }
  }
  if (allTargetsIllegal(game, obj)) {
    emit(game, event(EventKind.FIZZLED, { objectId: obj.id, player: obj.controller }));
    logRecord(game, "All targets illegal; ability is countered (CR 608.2b)");
    cease(game, obj);
    return;
  }
  const resolution = newResolution(game, obj.source || obj.id, obj.controller, {
    stackObject: obj,
    targets: obj.targets,
    xValue: obj.xValue,
    eventAmount: triggerEvent?.amount || 0,
    remembered: stillWhereExpected(game, ability, triggerEvent), // CR 603.7c
  });
  if (resolution.remembered.length === 0 && list(ability.remembered).length === 0) {
    resolution.remembered = triggerSubject(game, ability, triggerEvent);
  }
  // CR 107.3a: an X in an activation cost is the announced value.
  withAnnouncedX(game, obj.source, ability, obj.xValue, () => execute(resolution, list(ability.effects)));
  emit(game, event(EventKind.ABILITY_RESOLVED, { objectId: obj.id, player: obj.controller }));
  cease(game, obj);
}

/** What "it" means in a triggered ability: the object its event was about (CR 603.2, 608.2h). */
function triggerSubject(game: Game, ability: Ability, e: GameEvent | null): ObjectId[] {
  if (num(ability.kind) !== TRIGGERED || e === null) return [];
  // CR 603.12: a reflexive ability is about what its creator had just acted on.
  if (e.kind === ABILITY_TRIGGERED_EVENT && e.data.length > 0) return e.data.filter((d): d is number => typeof d === "number");
  const trigger = ability.trigger;
  if (trigger != null && trigger.subject == null && trigger.source != null) {
    // "Whenever a creature you control deals combat damage ... that creature": the cause.
    const cause = e.source ? game.objects.get(e.source) : undefined;
    return cause !== undefined ? [cause.id] : [];
  }
  const subject = subjectOf(game, e);
  return subject !== null ? [subject.id] : [];
}

/** CR 603.7c: a delayed ability's objects still in reach; it may follow only the move that triggered it. */
function stillWhereExpected(game: Game, ability: Ability, e: GameEvent | null): ObjectId[] {
  const remembered = list(ability.remembered) as readonly number[];
  if (remembered.length === 0) return [];
  const moved = new Set<number>();
  if (e !== null) {
    moved.add(e.objectId);
    for (const d of e.data) if (typeof d === "number") moved.add(d);
  }
  const out: ObjectId[] = [];
  for (const id of remembered) {
    let obj = game.objects.get(id);
    if (obj !== undefined && obj.supersededBy && moved.has(id)) obj = game.objects.get(obj.supersededBy);
    if (obj !== undefined && isLive(obj)) out.push(obj.id);
  }
  return out;
}

/** A finished ability stops existing (CR 608.2m). */
function cease(game: Game, obj: GameObject): void {
  game.objects.delete(obj.id);
}

// -- spells ---------------------------------------------------------------------------

function resolveSpell(game: Game, obj: GameObject): void {
  if (obj.bestowed && allTargetsIllegal(game, obj)) {
    // CR 702.103e, 608.3b: it resolves as the creature spell it is.
    obj.bestowed = false;
    obj.targets = [];
    invalidateAllCharacteristics(game);
  }
  const chars = characteristicsOf(game, obj);
  if (allTargetsIllegal(game, obj)) {
    emit(game, event(EventKind.FIZZLED, { objectId: obj.id, player: obj.controller }));
    logRecord(game, "All targets illegal; spell is countered (CR 608.2b)");
    moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
    return;
  }
  if (isPermanentType(chars.typeLine)) {
    // CR 608.3: a permanent spell resolves by becoming a permanent.
    const permanent = moveObject(game, obj, Zone.BATTLEFIELD, { toPlayer: obj.controller });
    permanent.controller = obj.controller;
    permanent.baseController = obj.baseController; // CR 110.2b
    if (obj.bestowed) {
      permanent.bestowed = true; // CR 702.103b
      invalidateAllCharacteristics(game);
    }
    carryEffectsOntoThePermanent(game, obj, permanent);
    carryLinks(game, obj.id, permanent.id); // CR 607.2q
    permanent.xValue = obj.xValue;
    applyEntersWithCounters(game, permanent);
    attachAuraOnEntry(game, obj, permanent);
    recordResolution(game, obj, chars);
    if (obj.entersTappedAndAttacking) {
      permanent.tapped = true; // CR 702.190b, 506.3a
      enterAttacking(game, permanent, { like: obj.entersAttackingLike });
    }
    onPermanentSpellResolved(game, permanent); // CR 702.109a, 702.152a, 702.185a
    emit(game, event(EventKind.SPELL_RESOLVED, { objectId: permanent.id, player: obj.controller }));
    return;
  }
  const resolution = newResolution(game, obj.id, obj.controller, { stackObject: obj, targets: obj.targets, xValue: obj.xValue });
  // CR 608.2, 702.55a: "put into a graveyard during its resolution".
  const outer = game.resolvingSpell;
  game.resolvingSpell = obj.id;
  try {
    resolveNonpermanent(game, obj, chars, resolution);
  } finally {
    game.resolvingSpell = outer;
  }
}

/** CR 608.2c-m: an instant or sorcery follows its instructions, then leaves the stack. */
function resolveNonpermanent(game: Game, obj: GameObject, chars: Characteristics, resolution: Resolution): void {
  for (const ability of chars.abilities as readonly Ability[]) {
    if (num(ability.kind) === SPELL) execute(resolution, list(ability.effects));
  }
  recordResolution(game, obj, chars);
  emit(game, event(EventKind.SPELL_RESOLVED, { objectId: obj.id, player: obj.controller }));
  // CR 608.2m: moved only if still there ("exile this spell" already moved it).
  if (!isLive(obj)) return;
  // CR 715.3d Adventure to exile, CR 720.3d Omen to the library; CR 702.27a buyback.
  let destination = RESOLUTION_ZONE.get(obj.castMode) ?? null;
  if (destination === null) destination = resolutionDestination(game, obj);
  if (destination === null) destination = Zone.GRAVEYARD;
  const moved = moveObject(game, obj, destination, { toPlayer: obj.owner });
  if (obj.castMode === CastMode.ADVENTURE) {
    moved.playableFromHereBy = obj.controller; // CR 715.3d: the creature half
    moved.playableFace = 0;
  }
}

/** Count this resolution against its controller and name (CR 702.192a), after it ran. */
function recordResolution(game: Game, spell: GameObject, chars: Characteristics): void {
  const key = `${spell.controller}|${chars.name.toLowerCase()}`;
  game.spellResolutions.set(key, (game.spellResolutions.get(key) ?? 0) + 1);
}

/** CR 303.4: an Aura enters attached to the target it was cast with (or a player, CR 303.4b). */
function attachAuraOnEntry(game: Game, spell: GameObject, permanent: GameObject): void {
  if (auraTargetEffect(characteristicsOf(game, permanent)) === null) return;
  const hostId = spell.targets[0]?.[0];
  if (hostId === undefined) return;
  if (isPlayerTarget(hostId)) {
    const pid = targetPlayer(hostId);
    if (!player(game, pid).hasLost) attachToPlayer(game, permanent, pid);
    return;
  }
  const host = game.objects.get(hostId);
  // CR 608.2b: the target is gone; the SBAs handle the Aura.
  if (host === undefined || host.zone !== Zone.BATTLEFIELD) return;
  attach(game, permanent, host);
}

/** CR 112.4: an effect naming the permanent spell names the permanent it became. */
function carryEffectsOntoThePermanent(game: Game, spell: GameObject, permanent: GameObject): void {
  let changed = false;
  for (const ce of game.continuousEffects) {
    const effect = ce.effect as Effect;
    const spec = effect.targets;
    const specific = list(spec?.specific);
    if (spec == null || specific.length === 0 || !specific.includes(spell.id)) continue;
    const zones = new Set(list((spec.zones as { $set?: number[] } | undefined)?.$set ?? [Zone.BATTLEFIELD]));
    zones.add(Zone.BATTLEFIELD);
    ce.effect = { ...effect, targets: { ...spec, specific: [...specific, permanent.id], zones: { $set: [...zones].sort((a, b) => a - b) } } };
    changed = true;
  }
  if (changed) invalidateAllCharacteristics(game);
}

/** CR 306.5b, 310.4: loyalty and defense arrive as counters. */
function applyEntersWithCounters(game: Game, permanent: GameObject): void {
  const chars = characteristicsOf(game, permanent);
  if (hasType(chars, CardType.PLANESWALKER) && chars.loyalty !== null && counterCount(permanent, "loyalty") === 0) {
    addCounters(permanent, "loyalty", chars.loyalty);
  }
  if (hasType(chars, CardType.BATTLE) && chars.defense !== null && counterCount(permanent, "defense") === 0) {
    addCounters(permanent, "defense", chars.defense);
  }
  invalidateAllCharacteristics(game);
}

// -- target legality (CR 608.2b) --------------------------------------------------------

/** Countered only when every target is illegal; no targets never fizzles. */
export function allTargetsIllegal(game: Game, obj: GameObject): boolean {
  if (obj.targets.length === 0) return false;
  const effects: readonly Effect[] = obj.ability !== null
    ? list(obj.ability.effects)
    : (characteristicsOf(game, obj).abilities as readonly Ability[]).filter((a) => num(a.kind) === SPELL).flatMap((a) => [...list(a.effects)]);
  // CR 700.2c: only the chosen modes' slots exist.
  const nodes = targetedNodes(effects, obj.chosenModes.length > 0 ? obj.chosenModes : null);
  if (nodes.length === 0) return false;
  for (let index = 0; index < nodes.length && index < obj.targets.length; index += 1) {
    const effect = nodes[index]!;
    for (const id of obj.targets[index]!) {
      if (isPlayerTarget(id)) {
        if (!player(game, targetPlayer(id)).hasLost) return false; // CR 115.4
        continue;
      }
      const candidate = game.objects.get(id);
      if (candidate === undefined) continue;
      if (effect.targets == null || matchHooks().matches(game, candidate, effect.targets, { source: obj.source || obj.id, controller: obj.controller })) {
        return false;
      }
    }
  }
  return true;
}

// -- putting abilities on the stack -----------------------------------------------------

/** Put an activated or triggered ability on the stack (CR 113.7), with its targets and modes. */
export function pushAbility(
  game: Game,
  source: GameObject,
  ability: Ability,
  options: { targets?: readonly (readonly number[])[]; xValue?: number; chosenModes?: readonly number[] } = {},
): GameObject {
  const obj = newGameObject(allocateObjectId(game.ids), {
    kind: ObjectKind.ABILITY,
    owner: source.controller,
    controller: source.controller,
    zone: Zone.STACK,
    timestamp: allocateTimestamp(game.ids),
    ability,
    source: source.id,
    targets: (options.targets ?? []).map((g) => g.slice()),
    xValue: options.xValue ?? 0,
    chosenModes: (options.chosenModes ?? []).slice(),
  });
  game.objects.set(obj.id, obj);
  game.stack.push(obj.id);
  emit(game, event(EventKind.PUT_ON_STACK, { objectId: obj.id, player: source.controller, source: source.id }));
  return obj;
}

