/**
 * Carrying out effects as a spell or ability resolves (CR 608). Ported from
 * mtgfish/rules/cr600_spells_and_abilities/resolve.py.
 *
 * Ported so far: the Resolution record, the dispatcher (`execute`), the
 * player and amount helpers, and the executors registered below (SEQUENCE,
 * ADD_MANA) with `resolveManaAbility`. PORT: the other executors, `_objects`
 * and the "it" bookkeeping of `_execute_one` are the "Resolving the stack"
 * item of ROADMAP "rules". As in Python, an effect kind with no executor is
 * logged as not executed and does nothing.
 */

import { PlayerScopeValues } from "../../ir";
import type { Ability, Condition, Cost, CostComponent, Effect, ObjectFilter, PlayerFilter, Restriction, TokenSpec, TriggerCondition, Value } from "../../ir";
import {
  becomeChosenCharacteristics,
  newReplacementEffect,
  register as registerReplacementEffect,
  ReplacementKind,
  replacementFromEffect,
} from "./replacement";
import { CONSUMING_COSTS, CostKind } from "../cr100_game_concepts/costs";
import { startEngines } from "../cr100_game_concepts/player";
import { Color, Duration, Layout, LossReason, Phase, Step, Supertype, Zone } from "../../kernel/enums";
import { becomeSolved, classLevel, flip, setClassLevel } from "../cr300_card_types/card_types";
import { venture } from "../cr300_card_types/dungeons";
import { sacrificeBlockersAtEndOfCombat, tempt } from "../cr700_additional_rules/ring";
import { action, ActionKind } from "../cr100_game_concepts/priority";
import { timesPaid, WARPED } from "../cr700_additional_rules/paid_costs";
import { delayedTrigger, pendingTrigger } from "./triggers";
import { CastError } from "./cast_error";
import { castSpell, playLand } from "./casting";
import { targetedNodes } from "./targeting";
import { turnFaceUp } from "../cr700_additional_rules/face_down";
import { isStillPaired, pair } from "../cr700_additional_rules/soulbond";
import { randint, randrange, shuffle } from "../../kernel/random";
import { catalogCard, conjure, conjureDuplicate, conjuredName, PERPETUAL_KINDS, perpetually, seek } from "../cr700_additional_rules/digital_mechanics";
import { enterAttacking } from "../cr500_turn_structure/combat";
import { copyTokenCard, createEmblem, createTokens } from "../cr100_game_concepts/tokens";
import { registerOverride, Rule } from "../cr100_game_concepts/rule_overrides";
import { registerStanding } from "../cr500_turn_structure/restrictions";
import { event, EventKind, type GameEvent } from "../../kernel/events";
import {
  agentFor,
  apnapOrder,
  controlPlayer,
  createObject,
  invalidateAllCharacteristics,
  livingPlayers,
  logRecord,
  nextPlayer,
  permanents,
  player,
  releasePlayer,
  turnOrderFrom,
  type Game,
} from "../../kernel/game";
import { becomePrepared } from "../cr700_additional_rules/preparation";
import { takeInitiative } from "../cr700_additional_rules/designations";
import { addCounters as putObjectCounters, counterCount, isLive, isPermanent, ObjectKind, objectText, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { allocateTimestamp, isPlayerTarget, NO_OBJECT, NO_PLAYER, targetPlayer, type ObjectId, type PlayerId } from "../../kernel/ids";
import { charsManaValue, hasKeyword, hasSupertype, isCreature, isLand } from "../cr200_parts_of_a_card/characteristics";
import {
  addCounters,
  addPoison,
  attach,
  bounce,
  detach,
  removeCounters,
  dealDamage,
  destroy,
  discard,
  exile,
  draw,
  gainLife,
  loseLife,
  mill,
  playerLoses,
  playerWins,
  recordLink,
  shuffleLibrary,
  sacrifice,
  tap,
  untap,
} from "../cr100_game_concepts/actions";
import { moveObject, removeFromZone } from "../zones";
import { chooseNewTargets, copyPermanent, copySpell, MAY_CHOOSE_NEW_TARGETS, transform } from "../cr700_additional_rules/faces";
import { canPayCost, payCost } from "./paying";
import { Act, registerStandingRequirement, requirement } from "../cr500_turn_structure/restrictions";
import { commanderColorIdentity } from "../cr903_commander/color_identity";
import { emit } from "../emit";
import { flag, ir, irEqual, list, num, str } from "../ir_read";
import { parseManaSymbol } from "../mana/cost";
import { addMana, manaKind, manaKindKey, poolSnapshot, type ManaKind, type ManaRestriction } from "../mana/pool";
import { constantOf, isTargeted, targetsAPlayer, targetsItsPlayer, walk } from "./effect_props";
import { characteristicsOf, copiableCharacteristics, EFFECT_LAYERS, intrinsicAbilities, layerFor, printedCharacteristics } from "./layers";
import { attackers } from "../cr500_turn_structure/combat";

const EK = {
  SEQUENCE: ir("EffectKind.SEQUENCE") as number,
  DESTROY: ir("EffectKind.DESTROY") as number,
  RESTRICTION: ir("EffectKind.RESTRICTION") as number,
  ADD_MANA: ir("EffectKind.ADD_MANA") as number,
  DAMAGE: ir("EffectKind.DAMAGE") as number,
  LOSE_LIFE: ir("EffectKind.LOSE_LIFE") as number,
  GAIN_LIFE: ir("EffectKind.GAIN_LIFE") as number,
  FIGHT: ir("EffectKind.FIGHT") as number,
  SACRIFICE: ir("EffectKind.SACRIFICE") as number,
};
const PS = PlayerScopeValues as Record<string, number>;

/** What one resolution knows as it carries out its effects. */
export interface Resolution {
  game: Game;
  source: ObjectId;
  controller: PlayerId;
  stackObject: GameObject | null;
  /** Targets chosen at announcement, one group per targeting node. */
  targets: readonly (readonly number[])[];
  xValue: number;
  eventAmount: number;
  /** What the resolution last acted on: "it", "that creature" (CR 608.2c). */
  remembered: ObjectId[];
  /** The next targeting node's index into `targets`. */
  targetIndex: number;
  /** A mana ability's modes, with no stack object to carry them (CR 605.3b). */
  chosenModes: readonly number[];
  dieResults: readonly number[];
  /** The colour chosen for "add one mana of any color" (CR 106.1); NONE for the first. */
  manaColor: Color;
  thatPlayer: PlayerId;
  /** What this resolution has done so far, by event kind ("this way"). */
  thisWay: Map<number, number>;
  /** Whether the last "you may" was carried out (CR 603.9, 608.2d). */
  didIt: boolean | null;
  pile: ObjectId[];
  /** The triggering event, with no stack object to carry it (CR 605.4a). */
  triggerEvent: GameEvent | null;
  chosenPlayers: PlayerId[];
  /** CR 701.19c: set while a "can't be regenerated" destroy runs. */
  unregenerable: boolean;
}

export function newResolution(
  game: Game,
  source: ObjectId,
  controller: PlayerId,
  fields: Partial<Omit<Resolution, "game" | "source" | "controller">> = {},
): Resolution {
  return {
    game,
    source,
    controller,
    stackObject: null,
    targets: [],
    xValue: 0,
    eventAmount: 0,
    remembered: [],
    targetIndex: 0,
    chosenModes: [],
    dieResults: [],
    manaColor: Color.NONE,
    thatPlayer: NO_PLAYER,
    thisWay: new Map(),
    didIt: null,
    pile: [],
    triggerEvent: null,
    chosenPlayers: [],
    unregenerable: false,
    ...fields,
  };
}

/** The targets chosen for this effect; advances the cursor (same_target reuses the last, CR 115.3). */
export function targetsFor(resolution: Resolution, effect: Effect): readonly number[] {
  if (!isTargeted(effect)) return [];
  if (flag(effect.same_target)) {
    const index = resolution.targetIndex - 1;
    return index >= 0 && index < resolution.targets.length ? resolution.targets[index]! : [];
  }
  if (resolution.targetIndex < resolution.targets.length) {
    const chosen = resolution.targets[resolution.targetIndex]!;
    resolution.targetIndex += 1;
    return chosen;
  }
  return [];
}

// -- dispatch -------------------------------------------------------------------

export type Executor = (resolution: Resolution, effect: Effect) => void;
const EXECUTORS = new Map<number, Executor>();

/** Register the executor for an effect kind (resolve.py's EXECUTORS table). */
export function registerExecutor(kind: number, executor: Executor): void {
  EXECUTORS.set(kind, executor);
}

/** Run effects in order (CR 608.2a); each is its own event batch (CR 608.2c, 603.2c). */
export function execute(resolution: Resolution, effects: readonly Effect[]): void {
  effects.forEach((effect, index) => {
    resolution.game.eventBatch += 1;
    // CR 701.19c: "Destroy all creatures. They can't be regenerated."
    if (num(effect.kind) === EK.DESTROY && forbidsRegeneratingThem(effects.slice(index + 1, index + 2))) {
      resolution.unregenerable = true;
      try {
        executeOne(resolution, effect);
      } finally {
        resolution.unregenerable = false;
      }
      return;
    }
    executeOne(resolution, effect);
  });
}

function forbidsRegeneratingThem(following: readonly Effect[]): boolean {
  const effect = following[0];
  if (effect === undefined || num(effect.kind) !== EK.RESTRICTION) return false;
  return list(effect.restrictions).some((r) => {
    const subject = r.subject ?? effect.targets;
    return num(r.act) === Act.BE_REGENERATED && subject != null && flag(subject.remembered);
  });
}

/** Leaves whose amounts "this way" can refer back to. */
const TALLIED = new Set([EK.DAMAGE, EK.LOSE_LIFE, EK.GAIN_LIFE, EK.FIGHT]);
const TALLY_KINDS = [EventKind.LIFE_LOST, EventKind.LIFE_GAINED, EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT] as number[];

/** This turn's running totals of the amounts "this way" asks about (per-player keys only). */
function tallyEvents(game: Game): Map<number, number> {
  const totals = new Map(TALLY_KINDS.map((k) => [k, 0]));
  for (const [key, amount] of game.turnHistory) {
    const parts = key.split("|");
    if (parts.length !== 2) continue;
    const kind = Number(parts[0]);
    if (totals.has(kind)) totals.set(kind, totals.get(kind)! + amount);
  }
  return totals;
}

export function executeOne(resolution: Resolution, effect: Effect): void {
  if (!TALLIED.has(num(effect.kind))) {
    executeOneInner(resolution, effect);
    return;
  }
  const before = tallyEvents(resolution.game);
  executeOneInner(resolution, effect);
  for (const [kind, total] of tallyEvents(resolution.game)) {
    const was = before.get(kind)!;
    if (total > was) resolution.thisWay.set(kind, (resolution.thisWay.get(kind) ?? 0) + total - was);
  }
}

function executeOneInner(resolution: Resolution, effect: Effect): void {
  // effects.py: every continuous kind with no executor of its own registers a continuous effect.
  const executor = EXECUTORS.get(num(effect.kind)) ?? (CONTINUOUS_KINDS.has(num(effect.kind)) ? registerContinuous : undefined);
  if (executor === undefined) {
    // No executor: UNPARSED, or a kind not implemented. It does nothing, loudly.
    logRecord(resolution.game, `Effect not executed (${num(effect.kind)}): ${str(effect.text) || "effect"}`, {
      kind: "unimplemented",
      player: resolution.controller,
    });
    return;
  }
  const kind = num(effect.kind);
  if (
    REMEMBERING.has(kind) &&
    effect.targets != null &&
    !isPerPlayerSacrifice(effect) &&
    !(NAMING_ONLY.has(kind) && flag(effect.targets.source_only))
  ) {
    // Captured before it runs: what dies to it is what the next sentence means (CR 400.7).
    // Peeking must not spend the target cursor.
    const cursor = resolution.targetIndex;
    const actedOn = objectsOf(resolution, effect).map((o) => o.id);
    resolution.targetIndex = cursor;
    // CR 608.2b: an illegal chosen target is still what a following "it" names.
    const chosenObjects = isTargeted(effect) && targetsFor(resolution, effect).some((id) => id >= 0);
    resolution.targetIndex = cursor;
    executor(resolution, effect);
    if (actedOn.length > 0 || chosenObjects) resolution.remembered = actedOn;
    return;
  }
  executor(resolution, effect);
}

/** Kinds whose subject a following sentence can call "it". */
const REMEMBERING: ReadonlySet<number> = new Set(
  [
    "DESTROY", "EXILE", "SACRIFICE", "TAP", "UNTAP", "BLOCKS_SOURCE_IF_ABLE", "RETURN_TO_HAND",
    "PUT_ONTO_BATTLEFIELD", "CREATE_TOKEN", "ADD_COUNTERS", "GAIN_CONTROL", "COPY_PERMANENT",
    "SEARCH_LIBRARY", "DAMAGE", "MOVE_ZONE", "PUT_ON_LIBRARY", "COUNTER_SPELL", "REGENERATE",
    "DOUBLE_COUNTERS", "CHANGE_TARGETS", "MODIFY_PT", "GRANT_ABILITY", "DESIGNATE", "RESTRICTION", "GOAD",
  ].map((n) => ir(`EffectKind.${n}` as never) as number),
);
/** Remembering kinds that only describe; about the source itself they leave "that spell" alone. */
const NAMING_ONLY: ReadonlySet<number> = new Set(
  ["MODIFY_PT", "GRANT_ABILITY", "RESTRICTION"].map((n) => ir(`EffectKind.${n}` as never) as number),
);

/** "Each opponent sacrifices a creature": named players choosing from their own. */
export function isPerPlayerSacrifice(effect: Effect): boolean {
  const spec = effect.targets;
  return (
    num(effect.kind) === EK.SACRIFICE &&
    effect.players != null &&
    (!isTargeted(effect) || targetsItsPlayer(effect)) &&
    spec != null &&
    !flag(spec.source_only) &&
    list(spec.specific).length === 0 &&
    !flag(spec.remembered)
  );
}

// -- selecting what an effect applies to ------------------------------------------------

/**
 * The objects an effect acts on (resolve.py `_objects`): targets re-checked
 * (an illegal one is skipped, CR 608.2b), "it" (CR 608.2), the trigger's
 * source, a targeted player's things, the source, or a fresh match.
 */
export function objectsOf(resolution: Resolution, effect: Effect, chosen: readonly number[] | null = null): GameObject[] {
  const game = resolution.game;
  const spec = effect.targets;
  const scope = { source: resolution.source, controller: resolution.controller };
  if (spec != null && flag(spec.from_pile)) return fromPile(resolution, effect);
  if (spec != null && flag(spec.remembered)) {
    const found: GameObject[] = [];
    for (const id of resolution.remembered) {
      let obj = game.objects.get(id);
      // CR 400.7: follow the object to what it became.
      while (obj !== undefined && obj.supersededBy) obj = game.objects.get(obj.supersededBy);
      if (obj !== undefined) found.push(obj);
    }
    return found;
  }
  if (spec != null && flag(spec.trigger_source)) {
    // "Counter that spell or ability" (CR 702.21a), while it is still where the filter says.
    const e = triggerEventOf(resolution);
    const obj = e !== null ? game.objects.get(e.source) : undefined;
    if (obj === undefined || !isLive(obj)) return [];
    const zones = list((spec.zones as { $set?: number[] } | undefined)?.$set ?? [Zone.BATTLEFIELD]);
    return zones.length === 0 || zones.includes(obj.zone) ? [obj] : [];
  }
  if (targetsItsPlayer(effect)) {
    // "Exile target player's graveyard": the player is the target; their things are found now.
    let players: Set<PlayerId>;
    if (chosen === null) {
      players = new Set(playersOf(resolution, effect));
    } else {
      players = new Set(targetedPlayers(chosen).filter((p) => !player(game, p).hasLost));
      resolution.chosenPlayers = [...players];
    }
    return matchHooks()
      .find(game, spec!, scope)
      .filter((obj) => players.has(obj.zone === Zone.BATTLEFIELD ? obj.controller : obj.owner));
  }
  if (isTargeted(effect)) {
    const out: GameObject[] = [];
    const picked = chosen ?? targetsFor(resolution, effect);
    for (const id of picked) {
      if (id < 0) continue; // a player target
      const obj = game.objects.get(id);
      if (obj === undefined) continue;
      if (spec != null && !matchHooks().matches(game, obj, spec, scope)) {
        logRecord(game, `Target #${obj.id} is no longer legal`, { kind: "fizzle" }); // CR 608.2b
        continue;
      }
      out.push(obj);
    }
    return out;
  }
  if (spec == null) {
    const obj = game.objects.get(resolution.source);
    return obj !== undefined ? [obj] : [];
  }
  let matching = matchHooks().find(game, spec, scope);
  if (playersSayWhose(effect)) {
    // "Exile each opponent's graveyard" (CR 108.3, 110.2).
    const theirs = new Set(playersOf(resolution, effect));
    matching = matching.filter((obj) => theirs.has(obj.zone === Zone.BATTLEFIELD ? obj.controller : obj.owner));
  }
  return narrowedToCount(resolution, effect, matching);
}

const WHOSE_KINDS: ReadonlySet<number> = new Set(
  ["EXILE", "REVEAL", "MOVE_ZONE", "PUT_ON_LIBRARY"].map((n) => ir(`EffectKind.${n}` as never) as number),
);

function playersSayWhose(effect: Effect): boolean {
  const scope = effect.players == null ? null : num(effect.players.scope);
  return WHOSE_KINDS.has(num(effect.kind)) && (scope === PS.EACH_OPPONENT || scope === PS.OPPONENT);
}

/** The cards of the pile a filter picks out ("from among them", "the rest"); zones ignored. */
function fromPile(resolution: Resolution, effect: Effect): GameObject[] {
  const game = resolution.game;
  const loose = { ...effect.targets!, from_pile: false, count: null, up_to: false };
  const candidates: GameObject[] = [];
  for (const id of resolution.pile) {
    const obj = game.objects.get(id);
    if (obj === undefined || !isLive(obj)) continue;
    const spec = { ...loose, zones: { $set: [obj.zone] } };
    if (matchHooks().matches(game, obj, spec, { source: resolution.source, controller: resolution.controller })) candidates.push(obj);
  }
  return narrowedToCount(resolution, effect, candidates);
}

/** What agents may implement to choose objects for an untargeted count (CR 608.2d). */
export interface ObjectChooser {
  chooseObjects?(game: Game, player: PlayerId, effect: Effect, candidates: GameObject[], count: number): readonly GameObject[] | null;
}

/** An untargeted effect that names a number affects that many, chosen by its controller. */
function narrowedToCount(resolution: Resolution, effect: Effect, matching: GameObject[]): GameObject[] {
  const spec = effect.targets;
  if (spec != null && spec.count == null && flag(spec.up_to) && matching.length > 0) {
    return anyNumberOf(resolution.game, resolution.controller, effect, matching);
  }
  if (spec == null || spec.count == null || matching.length <= 1) return matching;
  const wanted = valueOf(resolution, spec.count);
  if (wanted <= 0) return flag(spec.up_to) ? [] : matching; // CR 107.1b
  if (wanted >= matching.length) return matching;
  const agent = agentFor(resolution.game, resolution.controller) as ObjectChooser | null;
  if (agent?.chooseObjects !== undefined) {
    const picked = agent.chooseObjects(resolution.game, resolution.controller, effect, [...matching], wanted) ?? [];
    const kept = matching.filter((o) => picked.includes(o)).slice(0, wanted);
    if (kept.length === wanted) return kept;
  }
  return matching.slice(0, wanted);
}

/** "Any number of": the player picks from none to all (CR 107.1c, 608.2d); all with no agent. */
function anyNumberOf(game: Game, playerId: PlayerId, effect: Effect, matching: GameObject[]): GameObject[] {
  const agent = agentFor(game, playerId) as ObjectChooser | null;
  if (agent?.chooseObjects === undefined) return matching;
  const picked = agent.chooseObjects(game, playerId, effect, [...matching], matching.length);
  if (picked == null) return matching;
  return matching.filter((o) => picked.includes(o));
}

/** A Value against this resolution. */
export function valueOf(resolution: Resolution, value: Value | number): number {
  if (typeof value === "number") return value;
  return matchHooks().evaluate(resolution.game, value, evaluateContext(resolution));
}

/** Whether an effect's condition holds; "if it's blue" asks about what the guarded effect would act on. */
export function conditionHolds(resolution: Resolution, effect: Effect): boolean {
  const condition = effect.condition;
  if (condition == null || num(condition.kind) === 0) return true;
  let remembered: readonly ObjectId[] = resolution.remembered.slice();
  const first = list(effect.children)[0];
  if (asksAboutAffected(condition) && first !== undefined) {
    const cursor = resolution.targetIndex;
    remembered = objectsOf(resolution, first).map((o) => o.id);
    resolution.targetIndex = cursor;
  }
  return matchHooks().holds(resolution.game, condition, {
    source: resolution.source,
    controller: resolution.controller,
    remembered,
    event: (resolution.stackObject?.triggerEvent as GameEvent | null) ?? null,
  });
}

const AFFECTED_MATCHES: number = ir("ConditionKind.AFFECTED_MATCHES");
function asksAboutAffected(condition: Condition): boolean {
  return num(condition.kind) === AFFECTED_MATCHES || list(condition.operands).some(asksAboutAffected);
}

// -- who and how many -----------------------------------------------------------

/** The event a resolving triggered ability triggered on, if any. */
export function triggerEventOf(resolution: Resolution): GameEvent | null {
  if (resolution.stackObject === null) return resolution.triggerEvent;
  return (resolution.stackObject.triggerEvent as GameEvent | null) ?? null;
}

/** The controller or owner of an object as it last existed (CR 608.2h). */
function lastKnown(resolution: Resolution, id: ObjectId, owner: boolean): PlayerId {
  const obj = resolution.game.objects.get(id);
  if (obj === undefined) return NO_PLAYER;
  return owner ? obj.owner : obj.controller;
}

/** "Defending player" (CR 506.2, 802.2a), from the source or the triggering attacker. */
function defendingPlayersOf(resolution: Resolution): PlayerId[] {
  const attacks = attackers(resolution.game);
  if (attacks.size === 0) return [];
  const e = triggerEventOf(resolution);
  for (const id of [resolution.source, e?.objectId ?? NO_OBJECT]) {
    const defender = attacks.get(id);
    if (defender !== undefined) return [defender];
  }
  const defenders = [...new Set(attacks.values())];
  return defenders.length === 1 ? defenders : [];
}

const targetedPlayers = (chosen: readonly number[]): PlayerId[] => chosen.filter(isPlayerTarget).map(targetPlayer);
const living = (game: Game, pids: readonly PlayerId[]): PlayerId[] => pids.filter((p) => !player(game, p).hasLost);
const unique = <T>(items: readonly T[]): T[] => [...new Set(items)];

/** Players named by reference to something else the ability mentioned; null for other scopes. */
function referredPlayers(resolution: Resolution, effect: Effect): PlayerId[] | null {
  const spec = effect.players;
  if (spec == null) return null;
  const scope = num(spec.scope);
  if (scope === PS.REFERRED_PLAYER) return [];
  if (scope === PS.DEFENDING_PLAYER) return defendingPlayersOf(resolution);
  if (scope === PS.CHOSEN_PLAYER) return living(resolution.game, resolution.chosenPlayers);
  const e = triggerEventOf(resolution);
  if (scope === PS.TRIGGER_PLAYER) return e === null || e.player === NO_PLAYER ? [] : [e.player];
  if (scope === PS.TRIGGER_OBJECT_CONTROLLER || scope === PS.TRIGGER_OBJECT_OWNER) {
    if (e === null || e.objectId === NO_OBJECT) return [];
    const found = lastKnown(resolution, e.objectId, scope === PS.TRIGGER_OBJECT_OWNER);
    return found === NO_PLAYER ? [] : [found];
  }
  if (scope === PS.REMEMBERED_CONTROLLER || scope === PS.REMEMBERED_OWNER) {
    const owner = scope === PS.REMEMBERED_OWNER;
    return unique(resolution.remembered.map((id) => lastKnown(resolution, id, owner))).filter((p) => p !== NO_PLAYER);
  }
  if (scope === PS.AFFECTED_CONTROLLER) {
    // PORT: the guarded instruction's objects need `_objects`; its player
    // targets are read now, without spending the cursor.
    const guarded = list(effect.children)[0];
    if (guarded === undefined) return [];
    const cursor = resolution.targetIndex;
    const players = isTargeted(guarded) ? targetedPlayers(targetsFor(resolution, guarded)) : [];
    resolution.targetIndex = cursor;
    return living(resolution.game, unique(players));
  }
  return null;
}

/** The players an effect happens to (resolve.py `_players`). */
export function playersOf(resolution: Resolution, effect: Effect): PlayerId[] {
  if (targetsAPlayer(effect)) {
    // Chosen on announcement (CR 601.2c, 603.3d); gone players are illegal (CR 608.2b).
    const picked = living(resolution.game, targetedPlayers(targetsFor(resolution, effect)));
    resolution.chosenPlayers = picked.slice();
    return picked;
  }
  const referred = referredPlayers(resolution, effect);
  if (referred !== null) return referred;
  const scope = effect.players == null ? null : num(effect.players.scope);
  if (scope === PS.THAT_PLAYER) return resolution.thatPlayer === NO_PLAYER ? [] : [resolution.thatPlayer];
  if (scope === PS.TRIGGER_SOURCE_CONTROLLER) {
    const e = triggerEventOf(resolution); // CR 702.21a
    return e === null || e.sourceController === NO_PLAYER ? [] : [e.sourceController];
  }
  return matchHooks().resolvePlayers(resolution.game, effect.players ?? { $: "PlayerFilter", scope: PS.YOU as never }, {
    controller: resolution.controller,
    source: resolution.source,
  });
}

function evaluateContext(resolution: Resolution) {
  return {
    source: resolution.source,
    controller: resolution.controller,
    xValue: resolution.xValue,
    eventAmount: resolution.eventAmount,
    dieResults: resolution.dieResults,
    remembered: resolution.remembered.slice(),
    thisWay: resolution.thisWay,
  };
}

/** The effect's amount (or second amount), evaluated against the game. */
export function amountOf(resolution: Resolution, effect: Effect, second = false): number {
  const value = second ? effect.amount2 : effect.amount;
  if (value == null) return 0;
  return matchHooks().evaluate(resolution.game, value, evaluateContext(resolution));
}

/** The second amount, as Python's `_amount2` (no X). */
export function amount2Of(resolution: Resolution, effect: Effect): number {
  if (effect.amount2 == null) return 0;
  const { xValue: _x, ...context } = evaluateContext(resolution);
  return matchHooks().evaluate(resolution.game, effect.amount2, context);
}

/** How many, for an amount that is a count: a literal 0 means one (resolve.py `_count`). */
export function countOf(resolution: Resolution, effect: Effect): number {
  const constant = constantOf(effect.amount ?? { $: "Value" });
  if (constant !== null) return Math.max(1, constant);
  return Math.max(0, amountOf(resolution, effect));
}

// -- control flow -----------------------------------------------------------------

registerExecutor(EK.SEQUENCE, (resolution, effect) => execute(resolution, list(effect.children)));

// -- mana (CR 106) -----------------------------------------------------------------

/** IR's "spend only on" rider as the pool's restriction. */
export function manaRestrictionOf(effect: Effect): ManaRestriction | null {
  const r = effect.mana_restriction;
  return r == null ? null : { key: r.key, filter: r.filter ?? null };
}

/** One printed mana symbol as a unit of mana; a hybrid's first colour (deterministic). */
export function manaKindOf(symbol: string, options: { snow: boolean; restriction?: ManaRestriction | null; untilEndOfCombat?: boolean }): ManaKind {
  const parsed = parseManaSymbol(symbol.replace(/^\{+|\}+$/g, ""));
  return manaKind(firstColor(parsed.colors), {
    snow: options.snow,
    restriction: options.restriction ?? null,
    untilEndOfCombat: options.untilEndOfCombat ?? false,
  });
}

const COLOR_ORDER = [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN];
/** The first single colour of a set, in WUBRG order; NONE if empty. */
export function firstColor(colors: number): Color {
  return COLOR_ORDER.find((c) => (colors & c) !== 0) ?? Color.NONE;
}
/** The single colours of a set, in WUBRG order. */
export const singleColors = (colors: number): Color[] => COLOR_ORDER.filter((c) => (colors & c) !== 0);
export const ALL_SINGLE_COLORS: readonly Color[] = COLOR_ORDER;

/** The colours "any color" may produce for this player; narrowed to the commander's identity (CR 903.4, 903.4f). */
export function manaColorChoices(game: Game, controller: PlayerId, effect: Effect): Color {
  const colors = num(effect.colors);
  if (!flag(effect.colors_in_commander_identity)) return colors as Color;
  const identity = commanderColorIdentity(game, controller);
  return (identity === null ? Color.NONE : colors & identity) as Color;
}

export const isSnowSource = (game: Game, obj: GameObject | undefined): boolean =>
  obj !== undefined && hasSupertype(characteristicsOf(game, obj), Supertype.SNOW);

/** CR 106.1: add mana to a player's pool. */
function doAddMana(resolution: Resolution, effect: Effect): void {
  if (effect.players != null && num(effect.players.scope) !== PS.YOU) {
    // "That player adds {C}": the mana goes to the player named (CR 106.4).
    const saved = resolution.controller;
    try {
      for (const pid of playersOf(resolution, effect)) {
        resolution.controller = pid;
        doAddMana(resolution, { ...effect, players: null });
      }
    } finally {
      resolution.controller = saved;
    }
    return;
  }
  const game = resolution.game;
  const sourceObj = game.objects.get(resolution.source);
  const snow = isSnowSource(game, sourceObj); // CR 107.4h
  const pool = player(game, resolution.controller).manaPool;
  const restriction = manaRestrictionOf(effect);
  const produced = list(effect.mana_produced);

  if (produced.length > 0) {
    // "Add {B} for each Swamp you control": the printed symbols, once per count.
    const amount2 = effect.amount2 ?? { $: "Value" as const };
    const repeat = constantOf(amount2) === 0 ? 1 : Math.max(0, amount2Of(resolution, effect));
    const untilEndOfCombat = num(effect.duration) === Duration.END_OF_COMBAT; // CR 702.189a
    for (let i = 0; i < repeat; i += 1) {
      for (const symbol of produced) addMana(pool, manaKindOf(symbol, { snow, restriction, untilEndOfCombat }), 1);
    }
  } else if (flag(effect.colors_from_trigger)) {
    // "One mana of any type that land produced" (CR 106.12a); none without the event (CR 106.5).
    const e = triggerEventOf(resolution);
    const types: readonly number[] = e !== null && e.kind === EventKind.TAPPED_FOR_MANA ? (e.data as readonly number[]) : [];
    if (types.length === 0) return;
    const amount = countOf(resolution, effect);
    const chosen = types.includes(resolution.manaColor) ? resolution.manaColor : types[0]!;
    addMana(pool, manaKind(chosen as Color, { snow, restriction }), amount);
  } else if (flag(effect.colors_chosen)) {
    // "The chosen color", recorded as the permanent entered (CR 614.1b).
    const amount = countOf(resolution, effect);
    addMana(pool, manaKind((sourceObj?.chosenColor ?? 0) as Color, { snow, restriction }), amount);
  } else if (num(effect.colors)) {
    const menu = manaColorChoices(game, resolution.controller, effect);
    if (!menu) return; // CR 903.4f
    const amount = countOf(resolution, effect);
    const wanted = resolution.manaColor;
    const chosen = wanted && (menu & wanted) === wanted ? wanted : firstColor(menu);
    addMana(pool, manaKind(chosen, { snow, restriction }), amount);
  } else {
    addMana(pool, manaKind(Color.NONE, { snow, restriction }), countOf(resolution, effect));
  }
  emit(game, event(EventKind.MANA_ADDED, { player: resolution.controller, source: resolution.source }));
}
registerExecutor(EK.ADD_MANA, doAddMana);

/**
 * Resolve a mana ability at once, without the stack (CR 605.3b). With
 * `tapped` ({T} in its cost) and mana produced, its permanent was tapped
 * for mana (CR 106.12a): one event, its data the colours produced.
 */
export function resolveManaAbility(resolution: Resolution, effects: readonly Effect[], options: { tapped: boolean }): void {
  const game = resolution.game;
  const pool = player(game, resolution.controller).manaPool;
  const before = new Map(poolSnapshot(pool).map(([kind, n]) => [manaKindKey(kind), n]));
  execute(resolution, effects);
  if (!options.tapped) return;
  const produced = unique(
    poolSnapshot(pool)
      .filter(([kind, n]) => n > (before.get(manaKindKey(kind)) ?? 0))
      .map(([kind]) => kind.color as number),
  ).sort((a, b) => a - b);
  if (produced.length > 0) {
    emit(game, event(EventKind.TAPPED_FOR_MANA, {
      objectId: resolution.source, player: resolution.controller, source: resolution.source, data: produced,
    }));
  }
}

// -- control flow (resolve.py _do_conditional .. _do_nothing) -----------------------------

interface ResolutionAgent {
  chooseOptional?(game: Game, player: PlayerId, effect: unknown): boolean;
  chooseDiscard?(game: Game, player: PlayerId): ObjectId;
  choosePay?(game: Game, player: PlayerId, cost: unknown, effect: Effect): boolean;
}
const agentOf = (game: Game, playerId: PlayerId) => agentFor(game, playerId) as ResolutionAgent | null;

registerExecutor(ir("EffectKind.CONDITIONAL"), (resolution, effect) => {
  execute(resolution, conditionHolds(resolution, effect) ? list(effect.children) : list(effect.otherwise));
});

registerExecutor(ir("EffectKind.REPEAT"), (resolution, effect) => {
  const times = Math.max(0, amountOf(resolution, effect));
  for (let i = 0; i < times; i += 1) execute(resolution, list(effect.children));
});

/** "May" scopes still decided by the ability's controller. */
const CONTROLLER_CHOOSES: ReadonlySet<number> = new Set([PS.YOU!, PS.EACH_PLAYER!, PS.EACH_OPPONENT!, PS.OPPONENT!]);

/** "You may ...": the controller decides on resolution; with no agent, yes. */
registerExecutor(ir("EffectKind.OPTIONAL"), (resolution, effect) => {
  if (effect.players != null && !CONTROLLER_CHOOSES.has(num(effect.players.scope))) {
    anotherPlayerMay(resolution, effect);
    return;
  }
  const agent = agentOf(resolution.game, resolution.controller);
  if (agent?.chooseOptional === undefined || agent.chooseOptional(resolution.game, resolution.controller, effect)) {
    resolution.didIt = true;
    execute(resolution, list(effect.children));
  } else {
    resolution.didIt = false;
  }
});

/** "That player may ...": the player named chooses and carries it out as "you" (CR 608.2d). */
function anotherPlayerMay(resolution: Resolution, effect: Effect): void {
  const game = resolution.game;
  for (const pid of playersOf(resolution, effect)) {
    const agent = agentOf(game, pid);
    if (agent?.chooseOptional !== undefined && !agent.chooseOptional(game, pid, effect)) continue;
    const saved = resolution.controller;
    resolution.controller = pid;
    try {
      execute(resolution, list(effect.children));
    } finally {
      resolution.controller = saved;
    }
  }
}

/**
 * "... unless that player pays {1}." With no agent the default is to pay,
 * so tax effects don't flatter the deck under test.
 */
registerExecutor(ir("EffectKind.UNLESS_PAYS"), (resolution, effect) => {
  const game = resolution.game;
  const payers = playersOf(resolution, effect);
  if (payers.length === 0) {
    execute(resolution, list(effect.children));
    return;
  }
  for (const pid of payers) {
    const cost = effect.pay_cost;
    if (cost == null || !canPayCost(game, pid, cost, { source: resolution.source })) {
      execute(resolution, list(effect.children));
      continue;
    }
    const agent = agentOf(game, pid);
    const willing = agent?.choosePay === undefined ? true : agent.choosePay(game, pid, cost, effect);
    // A failed payment must not waive the effect.
    if (!(willing && payCost(game, pid, cost, { source: resolution.source }))) execute(resolution, list(effect.children));
  }
});

const doNothing: Executor = () => {};
registerExecutor(ir("EffectKind.NOTHING"), doNothing);
// A static ability read by the trigger collector rather than resolved.
registerExecutor(ir("EffectKind.EXTRA_TRIGGER"), doNothing);

// -- cards and zones ------------------------------------------------------------------------

registerExecutor(ir("EffectKind.DRAW"), (resolution, effect) => {
  const count = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) draw(resolution.game, pid, count);
});

registerExecutor(ir("EffectKind.MILL"), (resolution, effect) => {
  const count = amountOf(resolution, effect);
  const game = resolution.game;
  const milled: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) {
    const top = player(game, pid).library.slice(0, Math.max(0, count));
    mill(game, pid, count, { source: resolution.source });
    // The milled cards, as the objects they became, are the pile "from among them".
    for (const id of top) {
      let obj = game.objects.get(id);
      while (obj !== undefined && obj.supersededBy) obj = game.objects.get(obj.supersededBy);
      if (obj !== undefined && obj.zone === Zone.GRAVEYARD) milled.push(obj.id);
    }
  }
  resolution.pile = milled;
});

registerExecutor(ir("EffectKind.DESTROY"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    destroy(resolution.game, obj, { source: resolution.source, regenerate: !resolution.unregenerable });
  }
});

/**
 * Exile, and for an "until" exile arrange the return (CR 610.3). Python
 * `_do_exile`.
 */
registerExecutor(ir("EffectKind.EXILE"), (resolution, effect) => {
  const game = resolution.game;
  const untilSourceLeaves = num(effect.duration) === Duration.WHILE_SOURCE_PERSISTS;
  // CR 610.3a, 610.3b: the named event already happened - never exiled.
  if (untilSourceLeaves && !sourceIsOnTheBattlefield(resolution)) return;
  const returning: ObjectId[] = [];
  const keywords = list(effect.keywords);
  // CR 406.3: "exile it face down".
  const faceDown = keywords.includes("face down");
  for (const obj of objectsOf(resolution, effect)) {
    const exiled = exile(game, obj, { source: resolution.source, linkId: linkOf(resolution), faceDown });
    if (untilSourceLeaves) returning.push(exiled.id);
    if (keywords.includes(WARPED)) {
      // CR 702.185a: its owner may cast it once this turn has ended.
      exiled.playableFromHereBy = exiled.owner;
      exiled.playableFace = exiled.faceIndex;
      exiled.playableFromTurn = game.turn + 1;
    }
  }
  if (returning.length > 0) returnWhenTheSourceLeaves(resolution, returning);
});

function sourceIsOnTheBattlefield(resolution: Resolution): boolean {
  const source = resolution.game.objects.get(resolution.source);
  return source !== undefined && isPermanent(source);
}

/**
 * The second one-shot effect of an "until" exile (CR 610.3). DIVERGENCE (as
 * Python): set up as a delayed triggered ability (CR 603.7), so the return
 * uses the stack; what returns, when and to whom is right.
 */
function returnWhenTheSourceLeaves(resolution: Resolution, exiled: readonly ObjectId[]): void {
  const allZones = Object.values(Zone).filter((z) => typeof z === "number") as number[];
  createDelayedTrigger(
    resolution,
    {
      $: "TriggerCondition",
      event_kinds: { $set: [EventKind.LEAVES_BATTLEFIELD] },
      // Matched on last known information (CR 603.6e).
      subject: { $: "ObjectFilter", specific: [resolution.source], zones: { $set: [] } },
      functions_in: { $set: allZones },
      uses_last_known_information: true,
      text: "when the exiling permanent leaves the battlefield",
    },
    [
      {
        $: "Effect",
        kind: ir("EffectKind.PUT_ONTO_BATTLEFIELD"),
        targets: { $: "ObjectFilter", specific: exiled.slice(), zones: { $set: [Zone.EXILE] } },
        // CR 610.3c: under its owner's control.
        under_owners_control: true,
        text: "return the exiled card to the battlefield",
      },
    ],
  );
}

/** What each remembered object has become within this resolution (CR 400.7). */
function currentIncarnations(resolution: Resolution): ObjectId[] {
  const game = resolution.game;
  const out: ObjectId[] = [];
  for (const id of resolution.remembered) {
    let obj = game.objects.get(id);
    while (obj !== undefined && obj.supersededBy) obj = game.objects.get(obj.supersededBy);
    if (obj !== undefined) out.push(obj.id);
  }
  return out;
}

/**
 * CR 603.7: set up an ability that fires later. CR 610.2: creating it is all
 * the one-shot effect does. CR 603.7c: it remembers what this resolution
 * was talking about, as those objects are now.
 */
export function createDelayedTrigger(
  resolution: Resolution,
  trigger: TriggerCondition,
  effects: readonly Effect[],
  options: { repeating?: boolean } = {},
): void {
  resolution.game.delayedTriggers.push(
    delayedTrigger(trigger, effects, resolution.controller, resolution.source, {
      repeating: options.repeating ?? false,
      remembered: currentIncarnations(resolution),
    }),
  );
}

/** CR 603.7: "at the beginning of the next end step, sacrifice it". */
registerExecutor(ir("EffectKind.DELAYED_TRIGGER"), (resolution, effect) => {
  if (effect.trigger == null) return;
  createDelayedTrigger(resolution, effect.trigger, list(effect.children), { repeating: flag(effect.repeats) });
});

/**
 * CR 603.9: a reflexive triggered ability - "when you do". It triggers
 * because the preceding action happened, inside this resolution, carrying
 * what was acted on (CR 603.12) on its event.
 */
registerExecutor(ir("EffectKind.REFLEXIVE_TRIGGER"), (resolution, effect) => {
  if (resolution.didIt === false) return;
  const condition = effect.condition;
  const always = condition == null || num(condition.kind) === ir("ConditionKind.ALWAYS");
  if (resolution.remembered.length === 0 && !always) return;
  const ability: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    effects: list(effect.children).slice(),
    text: effect.text || "when you do",
  };
  resolution.game.pendingTriggers.push(
    pendingTrigger(
      resolution.source,
      ability,
      event(EventKind.ABILITY_TRIGGERED, { objectId: resolution.source, data: resolution.remembered.slice() }),
      resolution.controller,
    ),
  );
});

/**
 * A requirement (CR 509.1c) that each creature acted on blocks the source if
 * able, for the effect's duration; pinned to these objects (CR 400.7).
 */
registerExecutor(ir("EffectKind.BLOCKS_SOURCE_IF_ABLE"), (resolution, effect) => {
  const game = resolution.game;
  const source = game.objects.get(resolution.source);
  if (source === undefined || source.zone !== Zone.BATTLEFIELD) return;
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone !== Zone.BATTLEFIELD) continue;
    registerStandingRequirement(
      game,
      requirement(Act.BLOCK, {
        subject: { $: "ObjectFilter", specific: [obj.id] },
        counterpart: { $: "ObjectFilter", specific: [source.id] },
        source: source.id,
        controller: resolution.controller,
        text: effect.text || `${objectText(obj, characteristicsOf(game, obj).name)} blocks ${objectText(source, characteristicsOf(game, source).name)} if able`,
        duration: num(effect.duration),
        createdTurn: game.turn,
      }),
    );
  }
});

/** CR 701.49: venture into the dungeon, or into [quality] (701.49d), through dungeons.ts. */
registerExecutor(ir("EffectKind.VENTURE"), (resolution, effect) => {
  for (const playerId of playersOf(resolution, effect)) venture(resolution.game, playerId, effect.dungeon_quality ?? "");
});

/** CR 701.54: the Ring tempts each of these players (ring.ts). */
registerExecutor(ir("EffectKind.RING_TEMPTS"), (resolution, effect) => {
  for (const playerId of playersOf(resolution, effect)) tempt(resolution.game, playerId);
});

/** The Ring's third ability: the triggering attacker's blockers are sacrificed at end of combat (ring.ts). */
registerExecutor(ir("EffectKind.SACRIFICE_BLOCKERS_AT_END_OF_COMBAT"), (resolution) => {
  const e = (resolution.stackObject?.triggerEvent ?? null) as GameEvent | null;
  if (e === null) return;
  sacrificeBlockersAtEndOfCombat(resolution.game, e.objectId, resolution.controller);
});

registerExecutor(EK.SACRIFICE, (resolution, effect) => {
  const game = resolution.game;
  if (!isPerPlayerSacrifice(effect)) {
    for (const obj of objectsOf(resolution, effect)) sacrifice(game, obj, { source: resolution.source });
    return;
  }
  // CR 701.21a, 608.2d: each player sacrifices their own, their choice.
  const everything = matchHooks().find(game, effect.targets!, { source: resolution.source, controller: resolution.controller });
  const sacrificed: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) {
    const theirs = everything.filter((o) => o.controller === pid);
    for (const obj of sacrificeChoice(resolution, effect, theirs, pid)) {
      sacrificed.push(obj.id);
      sacrifice(game, obj, { source: resolution.source });
    }
  }
  if (sacrificed.length > 0) resolution.remembered = sacrificed;
});

/** What this player sacrifices: as many as said, their choice; else cheapest, then id. */
function sacrificeChoice(resolution: Resolution, effect: Effect, theirs: GameObject[], pid: PlayerId): GameObject[] {
  const spec = effect.targets!;
  const game = resolution.game;
  if (spec.count == null && flag(spec.up_to) && theirs.length > 0) return anyNumberOf(game, pid, effect, theirs);
  if (spec.count == null) return theirs;
  const wanted = valueOf(resolution, spec.count);
  if (wanted <= 0 || theirs.length === 0) return [];
  if (wanted >= theirs.length) return theirs;
  const agent = agentFor(game, pid) as ObjectChooser | null;
  if (agent?.chooseObjects !== undefined) {
    const picked = agent.chooseObjects(game, pid, effect, [...theirs], wanted) ?? [];
    const kept = theirs.filter((o) => picked.includes(o)).slice(0, wanted);
    if (kept.length === wanted) return kept;
  }
  return [...theirs]
    .sort((a, b) => charsManaValue(characteristicsOf(game, a)) - charsManaValue(characteristicsOf(game, b)) || a.id - b.id)
    .slice(0, wanted);
}

registerExecutor(ir("EffectKind.RETURN_TO_HAND"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) bounce(resolution.game, obj, { source: resolution.source });
});

registerExecutor(ir("EffectKind.MOVE_ZONE"), (resolution, effect) => {
  // KNOWN: F62 - Python's `effect.zone or Zone.GRAVEYARD` sends a LIBRARY
  // destination (0) to the graveyard; ported as is.
  const destination = effect.zone == null ? Zone.GRAVEYARD : num(effect.zone) || Zone.GRAVEYARD;
  const objects = objectsOf(resolution, effect);
  revealIfSaid(resolution, effect, objects); // CR 701.20
  for (const obj of objects) moveObject(resolution.game, obj, destination as Zone, { toPlayer: obj.owner });
});

function revealIfSaid(resolution: Resolution, effect: Effect, objects: readonly GameObject[]): void {
  if (!list(effect.keywords).includes("reveal")) return;
  for (const obj of objects) emit(resolution.game, event(EventKind.REVEALED, { objectId: obj.id, player: obj.owner }));
}

registerExecutor(ir("EffectKind.DISCARD"), (resolution, effect) => {
  const game = resolution.game;
  const count = amountOf(resolution, effect);
  const discarded: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) {
    const p = player(game, pid);
    const agent = agentOf(game, pid);
    for (let i = 0; i < count; i += 1) {
      if (p.hand.length === 0) break;
      const last = p.hand[p.hand.length - 1]!;
      const choice = agent?.chooseDiscard !== undefined ? agent.chooseDiscard(game, pid) : last;
      const obj = game.objects.get(choice) ?? game.objects.get(last)!;
      discarded.push(obj.id);
      discard(game, obj, { source: resolution.source });
    }
  }
  // CR 608.2: "if you discarded a nonland card this way".
  if (discarded.length > 0) resolution.remembered = discarded;
});

// -- tokens and emblems (CR 111, 114) ----------------------------------------------------

/** CR 111.1-2: create tokens for whoever the effect says; "them" names what was made (CR 608.2c). */
registerExecutor(ir("EffectKind.CREATE_TOKEN"), (resolution, effect) => {
  if (effect.token == null) return;
  const game = resolution.game;
  const count = countOf(resolution, effect);
  const spec = tokenSizeNow(resolution, effect.token);
  // CR 707.2: a token copy is built from the copiable values of each object copied.
  let cards: (unknown | null)[] = [null];
  if (spec.copy_of != null) cards = copyOriginals(resolution, effect).map((original) => copyTokenCard(game, original, spec));
  const made: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) {
    for (const card of cards) {
      const created = createTokens(game, spec, pid, count, { source: resolution.source, ...(card !== null ? { card } : {}) });
      for (const token of created) recordLink(game, resolution.source, token.id, linkOf(resolution)); // CR 607.2c
      made.push(...created.map((t) => t.id));
    }
  }
  if (made.length > 0) resolution.remembered = made;
});

/** CR 608.2h: an X/X token's size is worked out as the effect resolves, then fixed. */
function tokenSizeNow(resolution: Resolution, spec: TokenSpec): TokenSpec {
  const changes: Partial<TokenSpec> = {};
  for (const name of ["power", "toughness"] as const) {
    const value = spec[name];
    if (value != null && constantOf(value) === null) changes[name] = { $: "Value", constant: valueOf(resolution, value) };
  }
  return Object.keys(changes).length > 0 ? { ...spec, ...changes } : spec;
}

/** The objects a "token that's a copy of ..." copies (cr702_cost_keyword_rules.copy_originals). */
function copyOriginals(resolution: Resolution, effect: Effect): GameObject[] {
  const spec = effect.token!.copy_of!;
  if (flag(spec.source_only)) {
    const obj = resolution.game.objects.get(resolution.source); // CR 608.2h: as it last existed
    return obj !== undefined ? [obj] : [];
  }
  return objectsOf(resolution, { ...effect, targets: spec });
}

/**
 * CR 114.2: "[player] gets an emblem with [ability]". As in Python, not in
 * the executor table: there is no CREATE_EMBLEM effect kind yet.
 */
export function doCreateEmblem(resolution: Resolution, effect: Effect): void {
  for (const pid of playersOf(resolution, effect)) {
    createEmblem(resolution.game, pid, list(effect.granted_abilities), { text: effect.text ?? "" });
  }
}

/** CR 607.1: the link id of the ability resolving; zero for a spell. */
export function linkOf(resolution: Resolution): number {
  return num(resolution.stackObject?.ability?.link_id ?? 0);
}

// -- prohibitions and rule overrides from resolved spells (CR 101.1-2, 611.2b) -----------

/** A standing prohibition with its duration; pinned to what was targeted or named now. */
registerExecutor(ir("EffectKind.RESTRICTION"), (resolution, effect) => {
  // "Target player can't cast spells this turn": read once, the cursor moves on.
  const chosen = targetsAPlayer(effect) ? playersOf(resolution, effect) : null;
  let pinnedObjects: ObjectFilter | null = null;
  if (isTargeted(effect) && !targetsAPlayer(effect)) {
    const found = objectsOf(resolution, effect).map((o) => o.id);
    if (found.length === 0) return;
    pinnedObjects = { $: "ObjectFilter", specific: found, zones: { $set: [Zone.BATTLEFIELD] } };
  }
  for (let restriction of list(effect.restrictions)) {
    if (pinnedObjects !== null && (restriction.subject == null || irEqual(restriction.subject, effect.targets))) {
      restriction = { ...restriction, subject: pinnedObjects };
    }
    const players = restriction.players ?? effect.players ?? null;
    const pinned = chosen ?? (players !== null ? referredPlayers(resolution, { ...effect, players }) : null);
    if (pinned !== null) {
      // "That player can't ...": named now, since nothing could answer it later.
      for (const pid of pinned) {
        registerRestrictionFrom(resolution, effect, { ...restriction, players: { $: "PlayerFilter", scope: PS.SPECIFIC as never, specific: pid } });
      }
      continue;
    }
    registerRestrictionFrom(resolution, effect, restriction);
  }
});

function registerRestrictionFrom(resolution: Resolution, effect: Effect, restriction: Restriction): void {
  registerStanding(resolution.game, {
    $: "Restriction",
    act: restriction.act,
    subject: restriction.subject ?? effect.targets ?? null,
    players: restriction.players ?? effect.players ?? null,
    source: resolution.source,
    controller: resolution.controller,
    counterpart: restriction.counterpart ?? null,
    text: restriction.text || effect.text || "",
    duration: num(effect.duration),
    created_turn: resolution.game.turn,
  });
}

/** Switch a rule off for a duration (CR 101.1); a rule the engine can't name is logged. */
registerExecutor(ir("EffectKind.SUSPEND_RULE"), (resolution, effect) => {
  if (!effect.rule) return;
  if (!(Object.values(Rule) as string[]).includes(effect.rule)) {
    logRecord(resolution.game, `cannot suspend CR ${effect.rule}: not a rule this engine can switch off`, { kind: "unimplemented" });
    return;
  }
  registerOverride(resolution.game, {
    rule: effect.rule as Rule,
    subject: effect.targets ?? null,
    players: effect.players ?? null,
    source: resolution.source,
    controller: resolution.controller,
    text: effect.text ?? "",
    duration: num(effect.duration),
    createdTurn: resolution.game.turn,
  });
});

// -- dice (CR 706) ---------------------------------------------------------------------------

/** Roll a die with `sides` faces (CR 706.1); 1..sides. */
export function rollDie(game: Game, playerId: PlayerId, sides: number): number {
  if (sides < 1) return 0;
  const result = randint(game.rng, 1, sides);
  logRecord(game, `${player(game, playerId).name} rolls ${result} on a d${sides}`, { kind: "dice", player: playerId });
  return result;
}

export function rollDice(game: Game, playerId: PlayerId, count: number, sides: number): number[] {
  return Array.from({ length: Math.max(0, count) }, () => rollDie(game, playerId, sides)); // CR 107.1b
}

interface DieOutcome {
  readonly low: number;
  readonly high?: number;
  readonly effects?: readonly Effect[];
}
const covers = (o: DieOutcome, result: number): boolean => result >= o.low && ((o.high ?? 0) <= 0 || result <= o.high!);

/** Roll, drop ignored rolls (CR 706.6), add modifiers (CR 706.2), and run the results table (CR 706.3a). */
registerExecutor(ir("EffectKind.ROLL_DICE"), (resolution, effect) => {
  const game = resolution.game;
  const sides = num(effect.dice_sides);
  if (sides < 1) return;
  const natural = rollDice(game, resolution.controller, countOf(resolution, effect), sides);
  if (natural.length === 0) return;
  const ignore = num(effect.dice_ignore_lowest);
  const kept = ignore ? [...natural].sort((a, b) => a - b).slice(Math.max(0, ignore)) : natural;
  const results = kept.map((roll) => roll + num(effect.dice_modifier));
  resolution.dieResults = results;
  const outcomes = list(effect.outcomes) as unknown as DieOutcome[];
  if (outcomes.length === 0) return; // CR 706.4: what follows reads the number
  for (const result of results) {
    const outcome = outcomes.find((o) => covers(o, result));
    if (outcome !== undefined) execute(resolution, list(outcome.effects));
  }
});

// -- damage and life ---------------------------------------------------------------------

/** Which object deals an effect's damage: the source, or "it" when the text names it (CR 120.1). */
function damageDealer(resolution: Resolution, effect: Effect): [ObjectId | null, PlayerId] {
  if (effect.damage_source == null || !flag(effect.damage_source.remembered)) return [resolution.source, resolution.controller];
  for (const id of resolution.remembered) {
    const obj = resolution.game.objects.get(id);
    if (obj !== undefined) return [obj.id, obj.controller];
  }
  return [null, resolution.controller];
}

registerExecutor(EK.DAMAGE, (resolution, effect) => {
  const game = resolution.game;
  const amount = amountOf(resolution, effect);
  const [dealer, dealerController] = damageDealer(resolution, effect);
  // Read once, split by kind (CR 115.4); the cursor moves even with no dealer.
  const chosen = isTargeted(effect) ? targetsFor(resolution, effect) : [];
  if (dealer === null) return; // CR 608.2b
  const sourceObj = game.objects.get(dealer);
  const chars = sourceObj !== undefined ? characteristicsOf(game, sourceObj) : null;
  const deathtouch = chars !== null && hasKeyword(chars, "Deathtouch");
  const lifelink = chars !== null && hasKeyword(chars, "Lifelink");
  const options = { source: dealer, sourceController: dealerController, deathtouch, lifelink };
  for (const obj of objectsOf(resolution, effect, chosen)) dealDamage(game, obj, amount, options);
  let recipients = targetedPlayers(chosen);
  if (recipients.length > 0) resolution.chosenPlayers = recipients.slice(); // "that player"
  if (recipients.length === 0 && effect.players != null && !isTargeted(effect)) recipients = playersOf(resolution, effect);
  for (const pid of recipients) dealDamage(game, pid, amount, { source: dealer, sourceController: dealerController, lifelink });
});

registerExecutor(EK.GAIN_LIFE, (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) gainLife(resolution.game, pid, amount, { source: resolution.source });
});

registerExecutor(EK.LOSE_LIFE, (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) loseLife(resolution.game, pid, amount, { source: resolution.source });
});

registerExecutor(ir("EffectKind.SET_LIFE"), (resolution, effect) => {
  const wanted = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) {
    const delta = wanted - player(resolution.game, pid).life;
    if (delta > 0) gainLife(resolution.game, pid, delta, { source: resolution.source });
    else if (delta < 0) loseLife(resolution.game, pid, -delta, { source: resolution.source });
  }
});

registerExecutor(ir("EffectKind.ADD_POISON"), (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) addPoison(resolution.game, pid, amount);
});

/** A target chosen to act in the next instruction: reading it moves the cursor; "it" is remembered. */
registerExecutor(ir("EffectKind.DESIGNATE"), (resolution, effect) => {
  objectsOf(resolution, effect);
});

/** The creature told to fight: the source, "it", or one a description names. */
function fighter(resolution: Resolution, effect: Effect): GameObject[] {
  const game = resolution.game;
  const spec = effect.damage_source!;
  if (flag(spec.source_only)) {
    const obj = game.objects.get(resolution.source);
    return obj !== undefined ? [obj] : [];
  }
  if (flag(spec.remembered)) return objectsOf(resolution, { $: "Effect", kind: EK.FIGHT as never, targets: spec });
  return matchHooks().find(game, spec, { source: resolution.source, controller: resolution.controller });
}

/** CR 701.14a-c: each deals damage equal to its power to the other; neither if either can't. */
registerExecutor(EK.FIGHT, (resolution, effect) => {
  const game = resolution.game;
  let combatants: GameObject[];
  if (effect.damage_source != null) {
    const opponents = objectsOf(resolution, effect); // read first: the cursor moves regardless
    const fighters = fighter(resolution, effect);
    if (fighters.length !== 1 || opponents.length !== 1) return;
    combatants = [fighters[0]!, opponents[0]!];
  } else {
    combatants = objectsOf(resolution, effect);
    if (combatants.length !== 2) return;
  }
  const [first, second] = combatants as [GameObject, GameObject];
  if (first.zone !== Zone.BATTLEFIELD || second.zone !== Zone.BATTLEFIELD) return;
  const a = characteristicsOf(game, first);
  const b = characteristicsOf(game, second);
  if (!isCreature(a) || !isCreature(b)) return;
  dealDamage(game, second, a.power ?? 0, {
    source: first.id, sourceController: first.controller, deathtouch: hasKeyword(a, "Deathtouch"), lifelink: hasKeyword(a, "Lifelink"),
  });
  dealDamage(game, first, b.power ?? 0, {
    source: second.id, sourceController: second.controller, deathtouch: hasKeyword(b, "Deathtouch"), lifelink: hasKeyword(b, "Lifelink"),
  });
});

// -- permanents --------------------------------------------------------------------------

registerExecutor(ir("EffectKind.TAP"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) tap(resolution.game, obj, { source: resolution.source });
});

registerExecutor(ir("EffectKind.UNTAP"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) untap(resolution.game, obj, { source: resolution.source });
});

// -- counters, attaching, control, copies ------------------------------------------------------

registerExecutor(ir("EffectKind.ADD_COUNTERS"), (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  const objects = objectsOf(resolution, effect);
  const type = effect.counter_type ?? "";
  if (flag(effect.divided) && objects.length > 0) {
    // CR 601.2d: shared out as evenly as possible, the remainder to the first.
    const share = Math.floor(amount / objects.length);
    const extra = amount % objects.length;
    objects.forEach((obj, index) => {
      const given = share + (index < extra ? 1 : 0);
      if (given) addCounters(resolution.game, obj, type, given, { source: resolution.source });
    });
    return;
  }
  for (const obj of objects) addCounters(resolution.game, obj, type, amount, { source: resolution.source });
});

/** CR 701.10e: as many of those counters as it already has, as an ordinary placing (CR 614.1a). */
registerExecutor(ir("EffectKind.DOUBLE_COUNTERS"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    const kinds = effect.counter_type ? [effect.counter_type] : [...obj.counters.keys()];
    for (const kind of kinds) {
      const present = counterCount(obj, kind);
      if (present > 0) addCounters(resolution.game, obj, kind, present, { source: resolution.source });
    }
  }
});

registerExecutor(ir("EffectKind.REMOVE_COUNTERS"), (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const obj of objectsOf(resolution, effect)) {
    removeCounters(resolution.game, obj, effect.counter_type ?? "", amount, { source: resolution.source });
  }
});

/** CR 701.28: one more of each kind; by default all of your own and none of theirs. */
registerExecutor(ir("EffectKind.PROLIFERATE"), (resolution) => {
  const game = resolution.game;
  for (const obj of [...permanents(game, resolution.controller)]) {
    for (const kind of [...obj.counters.keys()]) addCounters(game, obj, kind, 1, { source: resolution.source });
  }
});

/** CR 701.3: `targets` is what it attaches to; what moves is the source unless named. */
registerExecutor(ir("EffectKind.ATTACH"), (resolution, effect) => {
  const game = resolution.game;
  let moving = game.objects.get(resolution.source);
  if (effect.attached_to != null) {
    const candidates = matchHooks().find(game, effect.attached_to, { source: resolution.source, controller: resolution.controller });
    if (candidates.length > 0) moving = candidates[0];
  }
  if (moving === undefined) return;
  for (const obj of objectsOf(resolution, effect)) attach(game, moving, obj);
});

registerExecutor(ir("EffectKind.UNATTACH"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) detach(resolution.game, obj);
});

/** Control change is a layer 2 continuous effect, not an immediate write. */
registerExecutor(ir("EffectKind.GAIN_CONTROL"), (resolution, effect) => registerContinuous(resolution, effect));

/** CR 712.18: turn it over, keeping the same object. */
registerExecutor(ir("EffectKind.TRANSFORM"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) transform(resolution.game, obj);
});

/** CR 707.1: make the source a copy of another permanent. */
registerExecutor(ir("EffectKind.COPY_PERMANENT"), (resolution, effect) => {
  const copier = resolution.game.objects.get(resolution.source);
  if (copier === undefined) return;
  for (const original of objectsOf(resolution, effect)) copyPermanent(resolution.game, copier, original);
});

/** CR 707.10: a copy on the stack, never cast; "the copy" is what's remembered. */
registerExecutor(ir("EffectKind.COPY_SPELL"), (resolution, effect) => {
  const count = countOf(resolution, effect);
  const made: ObjectId[] = [];
  for (const original of objectsOf(resolution, effect)) {
    let copier: PlayerId | null = resolution.controller;
    if (effect.players != null && num(effect.players.scope) === PS.OPPONENT) {
      copier = chooseCopyingOpponent(resolution, original); // CR 702.144a
      if (copier === null) continue;
    }
    for (let i = 0; i < count; i += 1) {
      const copy = copySpell(resolution.game, original, {
        controller: copier,
        mayChooseNewTargets: list(effect.keywords).includes(MAY_CHOOSE_NEW_TARGETS),
      });
      if (copy !== null) made.push(copy.id);
    }
  }
  if (made.length > 0) resolution.remembered = made;
});

/** CR 702.144a: the opponent who also copies it; the agent's pick, else the first in turn order. */
function chooseCopyingOpponent(resolution: Resolution, spell: GameObject): PlayerId | null {
  const game = resolution.game;
  const me = resolution.controller;
  const opponents = livingPlayers(game).map((p) => p.id).filter((p) => p !== me);
  if (opponents.length === 0) return null;
  const agent = agentFor(game, me) as { chooseCopyingOpponent?(g: Game, p: PlayerId, s: GameObject, o: readonly PlayerId[]): PlayerId } | null;
  const chosen = agent?.chooseCopyingOpponent?.(game, me, spell, opponents);
  return chosen !== undefined && opponents.includes(chosen) ? chosen : opponents[0]!;
}

registerExecutor(ir("EffectKind.GOAD"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    obj.goadedBy.add(resolution.controller);
    logRecord(resolution.game, `#${obj.id} is goaded`, { kind: "effect" });
  }
});

/** CR 701.5: off the stack and into its owner's graveyard; an ability simply ceases. */
registerExecutor(ir("EffectKind.COUNTER_SPELL"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone !== Zone.STACK) continue;
    if (hasKeyword(characteristicsOf(game, obj), "Can't be countered")) {
      logRecord(game, `#${obj.id} can't be countered`, { kind: "effect" });
      continue;
    }
    emit(game, event(EventKind.COUNTERED, { objectId: obj.id, source: resolution.source }));
    if (obj.kind === ObjectKind.ABILITY) {
      removeFromZone(game, obj);
      game.objects.delete(obj.id);
    } else {
      moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
    }
  }
});

// -- continuous effects from resolving spells and abilities (CR 611) -----------------------------

/** effects.py CONTINUOUS_KINDS: kinds with no executor of their own register a continuous effect. */
const CONTINUOUS_KINDS: ReadonlySet<number> = new Set(
  [
    "MODIFY_PT", "SET_PT", "SWITCH_PT", "GRANT_ABILITY", "REMOVE_ABILITIES", "SET_COLOR", "ADD_TYPE",
    "REMOVE_TYPE", "SET_TYPE", "TEXT_CHANGE", "RESTRICTION", "PERMISSION", "SUSPEND_RULE", "MODIFY_COST", "REPLACEMENT",
  ].map((n) => ir(`EffectKind.${n}` as never) as number),
);

/** CR 611.2c: effects on characteristics, and control changes, settle their set as they begin. */
function settlesItsSet(effect: Effect): boolean {
  return EFFECT_LAYERS.has(num(effect.kind)) || num(effect.kind) === ir("EffectKind.GAIN_CONTROL");
}

/** CR 608.2h: a resolving effect's numbers are worked out now, except those read per affected object. */
function lockedAmounts(resolution: Resolution, effect: Effect): Effect {
  const changes: Partial<Effect> = {};
  const fixedKinds = new Set([ir("ValueKind.CONSTANT") as number, ir("ValueKind.UNCHANGED") as number]);
  for (const name of ["amount", "amount2"] as const) {
    const value = effect[name] ?? { $: "Value" as const };
    if (fixedKinds.has(num(value.kind))) continue;
    const [affected] = matchHooks().valueSubjects(value);
    if (affected) continue;
    changes[name] = { $: "Value", constant: amountOf(resolution, effect, name === "amount2") };
  }
  return Object.keys(changes).length > 0 ? { ...effect, ...changes } : effect;
}

/** Create a continuous effect from a resolving spell or ability (CR 611.2), timestamped now. */
export function registerContinuous(resolution: Resolution, effect: Effect): void {
  const game = resolution.game;
  const duration = num(effect.duration) || Duration.PERMANENT;
  const push = (resolved: Effect): void => {
    game.continuousEffects.push({
      effect: resolved,
      source: resolution.source,
      controller: resolution.controller,
      timestamp: allocateTimestamp(game.ids),
      layer: layerFor(resolved),
      duration,
      createdTurn: game.turn,
      ability: null,
      fromStaticAbility: false,
      isCda: false,
      expired: false,
    });
  };
  if (num(effect.kind) === ir("EffectKind.GRANT_ABILITY") && effect.targets == null && effect.players != null) {
    // CR 702.11a, 702.16a: an ability a player has, registered per player, aimed at no object.
    for (const pid of playersOf(resolution, effect)) {
      push({
        ...effect,
        players: { $: "PlayerFilter", scope: effect.players.scope, specific: pid },
        targets: { $: "ObjectFilter", specific: [NO_OBJECT] },
      });
    }
    invalidateAllCharacteristics(game);
    return;
  }
  let resolved = effect;
  if (isTargeted(effect) || settlesItsSet(effect)) {
    const chosen = objectsOf(resolution, effect).map((o) => o.id);
    // An empty set stays empty: never register an empty `specific`, which means "anything".
    if (chosen.length === 0) return;
    // CR 112.4: the zones its objects are in now, plus the battlefield they may be heading for.
    const zones = new Set<number>(chosen.flatMap((id) => (game.objects.has(id) ? [game.objects.get(id)!.zone] : [])));
    zones.add(Zone.BATTLEFIELD);
    resolved = {
      ...effect,
      targets: { $: "ObjectFilter", specific: chosen, zones: { $set: [...zones].sort((a, b) => a - b) } },
      is_targeted: false,
    };
  }
  push(lockedAmounts(resolution, resolved));
  invalidateAllCharacteristics(game);
}

// -- players -------------------------------------------------------------------------------

registerExecutor(ir("EffectKind.PLAYER_WINS"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) playerWins(resolution.game, pid);
});

registerExecutor(ir("EffectKind.PLAYER_LOSES"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) playerLoses(resolution.game, pid, LossReason.EFFECT);
});

registerExecutor(ir("EffectKind.BECOME_MONARCH"), (resolution, effect) => {
  const game = resolution.game;
  for (const pid of playersOf(resolution, effect)) {
    for (const p of game.players) p.isMonarch = p.id === pid;
    emit(game, event(EventKind.BECAME_MONARCH, { player: pid }));
  }
});

/** "You may X. If you don't, ..." (CR 603.9 inverted): when the preceding option wasn't taken. */
registerExecutor(ir("EffectKind.IF_YOU_DONT"), (resolution, effect) => {
  if (resolution.didIt !== null) {
    if (resolution.didIt) return;
  } else if (resolution.remembered.length > 0) {
    return;
  }
  execute(resolution, list(effect.children));
});

const isDefaultZeroValue = (v: Value | undefined): boolean =>
  v === undefined || (num(v.kind) === 0 && num(v.constant) === 0 && list(v.operands).length === 0);

/** "You may pay {2}": success is remembered, for "if you do" (CR 603.9, 608.2d). */
registerExecutor(ir("EffectKind.PAY_COST"), (resolution, effect) => {
  const game = resolution.game;
  let cost = effect.pay_cost;
  if (cost == null) return;
  // CR 702.24a: "pay its upkeep cost for each age counter", as one payment.
  if (!isDefaultZeroValue(effect.amount)) cost = costTimes(cost, amountOf(resolution, effect));
  let paid = false;
  for (const pid of playersOf(resolution, effect)) {
    if (canPayCost(game, pid, cost) && payCost(game, pid, cost)) {
      resolution.remembered.push(resolution.source);
      paid = true;
    }
  }
  resolution.didIt = paid;
});

/** A cost charged `times` times over: mana summed, other amounts multiplied, paid whole or not at all. */
function costTimes(cost: Cost, times: number): Cost {
  if (times === 1) return cost;
  if (times <= 0) return { $: "Cost", components: [] };
  const manaSymbols: unknown[] = [];
  const others: CostComponent[] = [];
  for (const component of list(cost.components)) {
    if (num(component.kind) === CostKind.MANA) {
      for (let i = 0; i < times; i += 1) manaSymbols.push(...list(component.mana?.symbols));
    } else {
      let each: Value = component.amount ?? { $: "Value" };
      if (isDefaultZeroValue(component.amount) && CONSUMING_COSTS.has(num(component.kind))) each = { $: "Value", constant: 1 };
      others.push({ ...component, amount: { $: "Value", kind: ir("ValueKind.PRODUCT"), constant: times, operands: [each] } });
    }
  }
  if (manaSymbols.length > 0) others.unshift({ $: "CostComponent", kind: CostKind.MANA as never, mana: { $: "ManaCost", symbols: manaSymbols as never } });
  return { $: "Cost", components: others };
}

/** CR 702.179a: speed becomes 1 if it was 0. */
registerExecutor(ir("EffectKind.START_ENGINES"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) {
    if (startEngines(player(resolution.game, pid))) emit(resolution.game, event(EventKind.SPEED_INCREASED, { player: pid, amount: 1 }));
  }
});

registerExecutor(ir("EffectKind.ADD_ENERGY"), (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const pid of playersOf(resolution, effect)) {
    player(resolution.game, pid).energy += amount;
    emit(resolution.game, event(EventKind.ENERGY_GAINED, { player: pid, amount }));
  }
});

registerExecutor(ir("EffectKind.EXTRA_LAND_DROP"), (resolution, effect) => {
  const amount = Math.max(1, amountOf(resolution, effect));
  for (const pid of playersOf(resolution, effect)) player(resolution.game, pid).maxLands += amount;
});

registerExecutor(ir("EffectKind.EXTRA_TURN"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) resolution.game.extraTurns.push(pid);
});

// -- the library (CR 701.23) ---------------------------------------------------------------

/**
 * CR 701.23: search a library, put what's found where it says (the
 * library, zone 0, compared against null: "put that card on top" goes on
 * top after the shuffle), then shuffle.
 */
registerExecutor(ir("EffectKind.SEARCH_LIBRARY"), (resolution, effect) => {
  const game = resolution.game;
  const spec = effect.targets ?? null;
  const anyNumber = spec !== null && spec.count == null && flag(spec.up_to);
  const wanted = Math.max(1, amountOf(resolution, effect));
  const destination = effect.zone != null ? (num(effect.zone) as Zone) : Zone.HAND;
  const keywords = list(effect.keywords);
  for (const pid of playersOf(resolution, effect)) {
    const p = player(game, pid);
    let found: GameObject[] = [];
    for (const id of p.library.slice()) {
      const obj = game.objects.get(id);
      if (obj === undefined) continue;
      if (spec === null || matchHooks().matches(game, obj, spec, { source: resolution.source, controller: pid })) found.push(obj);
      if (!anyNumber && found.length >= wanted) break;
    }
    if (anyNumber && found.length > 0) found = anyNumberOf(game, pid, effect, found);
    const tapped = keywords.includes("tapped"); // CR 614.1c
    const onTop: GameObject[] = [];
    found.forEach((obj, index) => {
      const where = keywords.includes("other to hand") && index > 0 ? Zone.HAND : destination;
      if (where === Zone.LIBRARY) {
        onTop.push(obj);
        return;
      }
      const moved = moveObject(game, obj, where, { toPlayer: pid });
      if (tapped && where === Zone.BATTLEFIELD && moved.zone === Zone.BATTLEFIELD) moved.tapped = true;
    });
    emit(game, event(EventKind.SEARCHED_LIBRARY, { player: pid, amount: found.length }));
    if (onTop.length > 0) {
      for (const obj of onTop) p.library.splice(p.library.indexOf(obj.id), 1);
      shuffleLibrary(game, pid);
      for (const obj of [...onTop].reverse()) p.library.unshift(obj.id);
    } else {
      shuffleLibrary(game, pid);
    }
    resolution.remembered = found.map((o) => o.id);
  }
});

registerExecutor(ir("EffectKind.SHUFFLE"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) shuffleLibrary(resolution.game, pid);
});

// -- MTG Arena's digital-only mechanics (the parser's digital_mechanics.ts) -------------------

/** Seek: matching cards at random from the library, into the hand; "it" is what was found. */
registerExecutor(ir("EffectKind.SEEK"), (resolution, effect) => {
  const game = resolution.game;
  const count = countOf(resolution, effect);
  const found: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) {
    const toZone = effect.zone == null ? Zone.HAND : (num(effect.zone) as Zone);
    found.push(...seek(game, pid, effect.targets ?? null, count, { source: resolution.source, controller: resolution.controller, toZone }).map((o) => o.id));
  }
  resolution.remembered = found;
});

/** Conjure: by name from the catalogue, or a duplicate of an object (keeping perpetual changes). */
registerExecutor(ir("EffectKind.CONJURE"), (resolution, effect) => {
  const game = resolution.game;
  const zone = effect.zone == null ? Zone.HAND : (num(effect.zone) as Zone);
  const count = countOf(resolution, effect);
  const keywords = list(effect.keywords);
  const tapped = keywords.includes("tapped");
  const fromTop = constantOf(effect.amount2 ?? { $: "Value" }) === 0 ? 0 : amount2Of(resolution, effect);
  const made: ObjectId[] = [];
  const players = playersOf(resolution, effect);
  const name = conjuredName(effect);
  if (name) {
    const card = catalogCard(game, name);
    if (card !== null) {
      for (const pid of players) {
        for (let i = 0; i < count; i += 1) {
          const obj = conjure(game, pid, card, zone, { tapped, fromTop, source: resolution.source });
          if (obj !== null) made.push(obj.id);
        }
      }
    }
  } else if (effect.targets != null) {
    const originals = objectsOf(resolution, effect);
    const keepPerpetual = keywords.includes("duplicate");
    for (const pid of players) {
      for (const original of originals) {
        for (let i = 0; i < count; i += 1) {
          const obj = conjureDuplicate(game, pid, original, zone, { keepPerpetual, tapped, source: resolution.source });
          if (obj !== null) made.push(obj.id);
        }
      }
    }
  }
  resolution.remembered = made;
});

/** Perpetually: each change bound to each object for good; the set settled now (CR 611.2c). */
registerExecutor(ir("EffectKind.PERPETUALLY"), (resolution, effect) => {
  const game = resolution.game;
  const objects = perpetualObjects(resolution, effect);
  const changes = [...walk(effect)].filter((node) => PERPETUAL_KINDS.has(num(node.kind)));
  const pt = new Set([ir("EffectKind.MODIFY_PT") as number, ir("EffectKind.SET_PT") as number]);
  for (const obj of objects) {
    for (const change of changes) {
      const amounts: [number, number] | null = pt.has(num(change.kind)) ? [amountOf(resolution, change), amountOf(resolution, change, true)] : null;
      perpetually(game, obj, change, resolution.controller, { amounts });
    }
  }
  if (objects.length > 0) resolution.remembered = objects.map((o) => o.id);
});

/** "A random" one is picked with the game's generator. */
function perpetualObjects(resolution: Resolution, effect: Effect): GameObject[] {
  if (!list(effect.keywords).includes("at random") || effect.targets == null) return objectsOf(resolution, effect);
  const spec = effect.targets;
  const pool = matchHooks().find(resolution.game, { ...spec, count: null, up_to: false }, { source: resolution.source, controller: resolution.controller });
  const wanted = spec.count != null ? valueOf(resolution, spec.count) : 1;
  const picked: GameObject[] = [];
  while (pool.length > 0 && picked.length < wanted) picked.push(pool.splice(randrange(resolution.game.rng, pool.length), 1)[0]!);
  return picked;
}

// -- looking, revealing, putting (CR 701.18, 701.20, 701.44) ------------------------------------

/** CR 701.20b: revealing moves nothing. */
registerExecutor(ir("EffectKind.REVEAL"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) emit(resolution.game, event(EventKind.REVEALED, { objectId: obj.id, player: obj.owner }));
});

/** What agents may implement to decide which looked-at cards stay on top. */
interface TopOfLibraryChooser {
  chooseTopOfLibrary?(game: Game, player: PlayerId, looked: GameObject[], toGraveyard: boolean): readonly GameObject[];
}

/**
 * Scry (CR 701.18) and surveil (CR 701.44): look at the top N; the rejects
 * go to the bottom or the graveyard. With no agent, lands and cards of mana
 * value 3 or less stay.
 */
function lookAtTop(resolution: Resolution, effect: Effect, toGraveyard: boolean): void {
  const game = resolution.game;
  const count = Math.max(0, amountOf(resolution, effect));
  if (!count) return;
  for (const pid of playersOf(resolution, effect)) {
    const looked = player(game, pid).library.slice(0, count).filter((id) => game.objects.has(id)).map((id) => game.objects.get(id)!);
    if (looked.length === 0) continue;
    const agent = agentFor(game, pid) as TopOfLibraryChooser | null;
    const keep = agent?.chooseTopOfLibrary !== undefined
      ? agent.chooseTopOfLibrary(game, pid, looked, toGraveyard)
      : looked.filter((obj) => {
          const printed = printedCharacteristics(game, obj);
          return isLand(printed) || charsManaValue(printed) <= 3;
        });
    for (const obj of looked.filter((o) => !keep.includes(o))) {
      moveObject(game, obj, toGraveyard ? Zone.GRAVEYARD : Zone.LIBRARY, { toPlayer: pid });
    }
    emit(game, event(toGraveyard ? EventKind.SURVEILLED : EventKind.SCRIED, { player: pid, amount: count }));
  }
}
registerExecutor(ir("EffectKind.SCRY"), (resolution, effect) => lookAtTop(resolution, effect, false));
registerExecutor(ir("EffectKind.SURVEIL"), (resolution, effect) => lookAtTop(resolution, effect, true));

/**
 * Put a card onto the battlefield without casting it: tapped, attacking
 * (enterAttacking decides, CR 506.3, 508.4), face down (CR 708.3),
 * transformed (CR 712.14a), under its owner's control (CR 610.3c).
 */
registerExecutor(ir("EffectKind.PUT_ONTO_BATTLEFIELD"), (resolution, effect) => {
  const game = resolution.game;
  const keywords = list(effect.keywords);
  const tapped = keywords.includes("tapped");
  const attacking = keywords.includes("attacking");
  const paid = resolution.stackObject !== null ? resolution.stackObject.costPaidObjects.slice() : [];
  for (const obj of objectsOf(resolution, effect)) {
    if (!isLive(obj)) continue; // CR 400.7: the husk of something already moved
    const to = flag(effect.under_owners_control) ? obj.owner : resolution.controller;
    const permanent = moveObject(game, obj, Zone.BATTLEFIELD, {
      toPlayer: to,
      ...(keywords.includes("face down") ? { faceDown: "" } : {}),
      transformed: keywords.includes("transformed"),
    });
    permanent.controller = to;
    if (permanent !== obj) recordLink(game, resolution.source, permanent.id, linkOf(resolution)); // CR 607.2c
    if (effect.counter_type) putObjectCounters(permanent, effect.counter_type, Math.max(1, amountOf(resolution, effect)));
    invalidateAllCharacteristics(game);
    if (!isPermanent(permanent)) continue; // a replacement sent it elsewhere
    if (tapped) permanent.tapped = true;
    if (attacking) enterAttacking(game, permanent, { like: paid });
  }
});

/**
 * Put objects on the top or bottom of their owners' libraries, in the order
 * chosen ("random": shuffled). One already in that library moves within it:
 * not a zone change (CR 400.7). What was placed leaves the pile.
 */
registerExecutor(ir("EffectKind.PUT_ON_LIBRARY"), (resolution, effect) => {
  const game = resolution.game;
  const keywords = list(effect.keywords);
  const onTop = num(effect.amount?.constant) >= 0 && !keywords.includes("bottom");
  const objects = objectsOf(resolution, effect).slice();
  revealIfSaid(resolution, effect, objects);
  if (keywords.includes("random")) shuffle(game.rng, objects);
  if (onTop) objects.reverse();
  const placed = new Set<ObjectId>();
  for (const obj of objects) {
    placed.add(obj.id);
    const lib = player(game, obj.owner).library;
    if (isLive(obj) && obj.zone === Zone.LIBRARY && lib.includes(obj.id)) {
      lib.splice(lib.indexOf(obj.id), 1);
      if (onTop) lib.unshift(obj.id);
      else lib.push(obj.id);
      continue;
    }
    moveObject(game, obj, Zone.LIBRARY, { toPlayer: obj.owner, toTop: onTop });
  }
  if (placed.size > 0 && resolution.pile.length > 0) resolution.pile = resolution.pile.filter((id) => !placed.has(id));
});

/** "Look at / reveal the top N": no card moves; they become the pile and "them". */
registerExecutor(ir("EffectKind.LOOK_AT_TOP"), (resolution, effect) => {
  const game = resolution.game;
  const count = Math.max(0, amountOf(resolution, effect));
  const pile: ObjectId[] = [];
  for (const pid of playersOf(resolution, effect)) pile.push(...player(game, pid).library.slice(0, count));
  resolution.pile = pile;
  resolution.remembered = pile.slice();
  if (list(effect.keywords).includes("reveal")) {
    for (const id of pile) emit(game, event(EventKind.REVEALED, { objectId: id, player: game.objects.get(id)!.owner }));
  }
});

/** CR 701.40: reveal the top card; a land to hand, otherwise a +1/+1 counter. */
registerExecutor(ir("EffectKind.EXPLORE"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    const p = player(game, obj.controller);
    if (p.library.length === 0) continue;
    const top = game.objects.get(p.library[0]!)!;
    emit(game, event(EventKind.REVEALED, { objectId: top.id, player: p.id }));
    if (isLand(printedCharacteristics(game, top))) moveObject(game, top, Zone.HAND, { toPlayer: p.id });
    else addCounters(game, obj, "+1/+1", 1, { source: resolution.source });
    emit(game, event(EventKind.EXPLORED, { objectId: obj.id, player: p.id }));
  }
});

// -- replacement and prevention from resolving spells (the parser's replacement.ts) ---------------

/**
 * A replacement effect made by a resolution (CR 614.1). Unearth's "if it
 * would leave, exile it instead" is bound to the permanent and used up
 * once (CR 400.7); the rest go through registerReplacement, and a shape
 * with no replacement kind is a continuous effect as before.
 */
registerExecutor(ir("EffectKind.REPLACEMENT"), (resolution, effect) => {
  const spec = effect.targets;
  if (num(effect.replacement_kind) !== ReplacementKind.REDIRECT_ZONE_CHANGE || effect.zone == null || spec == null || !flag(spec.source_only)) {
    if (num(effect.replacement_kind)) registerReplacement(resolution, effect);
    else registerContinuous(resolution, effect);
    return;
  }
  for (const obj of objectsOf(resolution, effect)) {
    if (!isPermanent(obj)) continue;
    registerReplacementEffect(resolution.game, newReplacementEffect({
      kind: ReplacementKind.REDIRECT_ZONE_CHANGE,
      eventKinds: new Set([EventKind.ZONE_CHANGE]),
      source: resolution.source,
      controller: resolution.controller,
      subject: { $: "ObjectFilter", specific: [obj.id], zones: { $set: [Zone.BATTLEFIELD] } },
      destination: num(effect.zone) as Zone,
      oneShot: true,
      text: effect.text ?? "",
    }));
  }
});

/** CR 611.2, 614: lasts its duration whether or not its source survives; named objects settled now. */
function registerReplacement(resolution: Resolution, effect: Effect): void {
  const game = resolution.game;
  for (const [targets, players] of boundRecipients(resolution, effect)) {
    const built = replacementFromEffect({ ...effect, targets, players, is_targeted: false }, {
      source: resolution.source, controller: resolution.controller, duration: num(effect.duration), createdTurn: game.turn,
    });
    if (built !== null) registerReplacementEffect(game, built);
  }
}

type Recipient = [ObjectFilter | null, PlayerFilter | null];

/**
 * What a resolving replacement or prevention is about, one (objects,
 * players) pair per shield: targets and "it" become specific; with `each`,
 * one shield per object or player a mass filter matches now; otherwise the
 * filter stays live.
 */
function boundRecipients(resolution: Resolution, effect: Effect, each = false): Recipient[] {
  const one = (id: ObjectId): Recipient => [{ $: "ObjectFilter", specific: [id], zones: { $set: [] } }, null];
  const who = (pid: PlayerId): Recipient => [null, { $: "PlayerFilter", scope: PS.SPECIFIC as never, specific: pid }];
  const spec = effect.targets ?? null;
  if (isTargeted(effect)) {
    if (targetsAPlayer(effect)) return playersOf(resolution, effect).map(who);
    const chosen = targetsFor(resolution, effect);
    const objects = objectsOf(resolution, effect, chosen);
    return [...objects.map((o) => one(o.id)), ...targetedPlayers(chosen).map(who)];
  }
  if (spec !== null && flag(spec.remembered)) return objectsOf(resolution, effect).map((o) => one(o.id));
  if (each && spec !== null && !flag(spec.source_only)) {
    const pairs = objectsOf(resolution, effect).map((o) => one(o.id));
    if (effect.players != null) pairs.push(...playersOf(resolution, effect).map(who));
    return pairs;
  }
  if (each && spec === null && effect.players != null) return playersOf(resolution, effect).map(who);
  return [[spec, effect.players ?? null]];
}

/** CR 615: a prevention shield; -1 is "all", otherwise a size worked out now (CR 608.2h, 615.7). */
registerExecutor(ir("EffectKind.PREVENT_DAMAGE"), (resolution, effect) => {
  const game = resolution.game;
  const constant = constantOf(effect.amount ?? { $: "Value" });
  const everything = constant !== null && constant < 0;
  const size = everything ? -1 : amountOf(resolution, effect);
  if (!everything && size <= 0) return;
  for (const [targets, players] of boundRecipients(resolution, effect, !everything)) {
    const built = replacementFromEffect({ ...effect, targets, players, is_targeted: false }, {
      source: resolution.source, controller: resolution.controller, duration: num(effect.duration), createdTurn: game.turn,
    });
    if (built === null) continue;
    built.amount = size;
    built.oneShot = !everything;
    registerReplacementEffect(game, built);
  }
});

/** CR 614.9: damage that would be dealt to one thing is dealt to another. */
registerExecutor(ir("EffectKind.REDIRECT_DAMAGE"), (resolution, effect) => {
  const chosen = objectsOf(resolution, effect);
  if (chosen.length === 0) return;
  registerReplacementEffect(resolution.game, newReplacementEffect({
    kind: ReplacementKind.REDIRECT_DAMAGE,
    eventKinds: new Set([EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT]),
    source: resolution.source,
    controller: resolution.controller,
    redirectTo: chosen[0]!.id,
    text: effect.text || "redirect damage",
  }));
});

/** CR 208.2b by resolution: the choice made for the permanent the effect refers to. */
registerExecutor(ir("EffectKind.ENTERS_AS_CHOICE"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone === Zone.BATTLEFIELD) becomeChosenCharacteristics(resolution.game, obj, effect);
  }
});

// -- permanents and players, continued ---------------------------------------------------------

/** An effect turns a face-down permanent up (CR 708.7-8): no cost, so no megamorph counter. */
registerExecutor(ir("EffectKind.TURN_FACE_UP"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) turnFaceUp(resolution.game, obj);
});

/**
 * CR 702.26b: phased out, treated as not existing, back in at its
 * controller's next untap step. As in Python, CR 610.4 "phase out until
 * [event]" isn't implemented (the parser drops the "until").
 */
registerExecutor(ir("EffectKind.PHASE_OUT"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    obj.phasedOut = true;
    obj.phasingScheduled = true;
    invalidateAllCharacteristics(game);
    emit(game, event(EventKind.PHASED_OUT, { objectId: obj.id, player: obj.controller }));
  }
});

/** CR 701.15: a regeneration shield. */
registerExecutor(ir("EffectKind.REGENERATE"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) obj.regenerationShields += 1;
});

/** CR 701.31: if not monstrous, N +1/+1 counters and it becomes monstrous (CR 701.37b event). */
registerExecutor(ir("EffectKind.MONSTROSITY"), (resolution, effect) => {
  const amount = Math.max(1, amountOf(resolution, effect));
  for (const obj of objectsOf(resolution, effect)) {
    if (counterCount(obj, "monstrous")) continue;
    addCounters(resolution.game, obj, "+1/+1", amount, { source: resolution.source });
    putObjectCounters(obj, "monstrous", 1);
    emit(resolution.game, event(EventKind.BECAME_MONSTROUS, { objectId: obj.id, player: obj.controller, source: resolution.source }));
  }
});

/** CR 701.43: N +1/+1 counters, only if it has none. */
registerExecutor(ir("EffectKind.ADAPT"), (resolution, effect) => {
  const amount = Math.max(1, amountOf(resolution, effect));
  for (const obj of objectsOf(resolution, effect)) {
    if (counterCount(obj, "+1/+1")) continue;
    addCounters(resolution.game, obj, "+1/+1", amount, { source: resolution.source });
  }
});

/** CR 701.10: two permanents swap controllers, or neither does; the base moves too (no duration). */
registerExecutor(ir("EffectKind.EXCHANGE_CONTROL"), (resolution, effect) => {
  const objects = objectsOf(resolution, effect);
  if (objects.length !== 2) return; // CR 701.10c
  const [first, second] = objects as [GameObject, GameObject];
  [first.controller, second.controller] = [second.controller, first.controller];
  [first.baseController, second.baseController] = [second.baseController, first.baseController];
  first.summoningSick = second.summoningSick = true;
  first.newlyControlled = second.newlyControlled = true;
  invalidateAllCharacteristics(resolution.game);
  for (const obj of objects) emit(resolution.game, event(EventKind.CONTROL_CHANGED, { objectId: obj.id, player: obj.controller }));
});

/** CR 701.10: two life totals swap, as gains and losses. */
registerExecutor(ir("EffectKind.EXCHANGE_LIFE"), (resolution, effect) => {
  const players = playersOf(resolution, effect);
  if (players.length !== 2) return;
  const game = resolution.game;
  const [first, second] = players as [PlayerId, PlayerId];
  const a = player(game, first).life;
  const b = player(game, second).life;
  for (const [pid, wanted] of [[first, b], [second, a]] as const) {
    const current = player(game, pid).life;
    if (wanted > current) gainLife(game, pid, wanted - current, { source: resolution.source });
    else if (wanted < current) loseLife(game, pid, current - wanted, { source: resolution.source });
  }
});

/** CR 724: end the turn - the stack is exiled and the turn goes to cleanup. */
registerExecutor(ir("EffectKind.END_TURN"), (resolution) => {
  const game = resolution.game;
  for (const id of game.stack.slice()) {
    const obj = game.objects.get(id);
    if (obj !== undefined) {
      removeFromZone(game, obj);
      game.objects.delete(id);
    }
  }
  game.stack.length = 0;
  game.pendingTriggers.length = 0;
  game.endTurnRequested = true;
  logRecord(game, "The turn ends immediately", { kind: "turn" });
});

registerExecutor(ir("EffectKind.SKIP_STEP"), (resolution, effect) => {
  resolution.game.skippedSteps.add(amountOf(resolution, effect) as Step);
});

registerExecutor(ir("EffectKind.ADD_EXPERIENCE"), (resolution, effect) => {
  const amount = Math.max(1, amountOf(resolution, effect));
  for (const pid of playersOf(resolution, effect)) player(resolution.game, pid).experience += amount;
});

/** CR 701.64a-b: only a permanent, and it stays harnessed. */
registerExecutor(ir("EffectKind.HARNESS"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone === Zone.BATTLEFIELD && !obj.harnessed) {
      obj.harnessed = true;
      invalidateAllCharacteristics(resolution.game);
      logRecord(resolution.game, `#${obj.id} becomes harnessed`, { kind: "designation" });
    }
  }
});

/** Whether the controller takes an optional effect (resolve.py `_wants`). */
function wants(resolution: Resolution, effect: Effect): boolean {
  const agent = agentOf(resolution.game, resolution.controller);
  return agent?.chooseOptional === undefined ? true : !!agent.chooseOptional(resolution.game, resolution.controller, effect);
}

/** CR 702.95a, c: pair the soulbond creature with the creature that entered, or one its controller chooses. */
registerExecutor(ir("EffectKind.PAIR"), (resolution, effect) => {
  const game = resolution.game;
  const source = game.objects.get(resolution.source);
  if (source === undefined) return;
  const e = (resolution.stackObject?.triggerEvent as GameEvent | null) ?? null;
  let other = e !== null ? game.objects.get(e.objectId) : undefined;
  if (other === undefined || other.id === source.id) {
    const candidates = permanents(game, resolution.controller).filter(
      (o) => o.id !== source.id && isCreature(characteristicsOf(game, o)) && !isStillPaired(game, o),
    );
    if (candidates.length === 0) return;
    const agent = agentFor(game, resolution.controller) as { choosePair?(g: Game, p: PlayerId, s: GameObject, c: GameObject[]): GameObject } | null;
    other = agent?.choosePair?.(game, resolution.controller, source, candidates);
    if (other === undefined || !candidates.includes(other)) {
      const power = (o: GameObject) => characteristicsOf(game, o).power ?? 0;
      other = candidates.reduce((best, o) => (power(o) > power(best) || (power(o) === power(best) && o.id < best.id) ? o : best));
    }
  }
  if (wants(resolution, effect)) pair(game, source, other, resolution.controller);
});

/**
 * CR 700.3: separate into two piles (either may be empty), then a player
 * picks one. Defaults: alternate from the highest mana value down; choose
 * the pile with more total mana value.
 */
registerExecutor(ir("EffectKind.SEPARATE_INTO_PILES"), (resolution, effect) => {
  const game = resolution.game;
  const objects = objectsOf(resolution, effect);
  if (objects.length === 0) return;
  const ids = objects.map((o) => o.id);
  const worth = (id: ObjectId) => charsManaValue(characteristicsOf(game, game.objects.get(id)!));
  const separators = matchHooks().resolvePlayers(game, effect.actor ?? { $: "PlayerFilter", scope: PS.YOU as never }, { controller: resolution.controller });
  const separator = separators[0] ?? resolution.controller;
  const sepAgent = agentFor(game, separator) as { separatePiles?(g: Game, p: PlayerId, ids: ObjectId[]): ObjectId[] | null } | null;
  let first = sepAgent?.separatePiles?.(game, separator, ids.slice()) ?? null;
  if (first === null) first = [...ids].sort((a, b) => worth(b) - worth(a) || a - b).filter((_, i) => i % 2 === 0);
  const inFirst = new Set(first);
  const pileA = ids.filter((id) => inFirst.has(id));
  const pileB = ids.filter((id) => !inFirst.has(id));
  logRecord(game, `Piles: ${pileA.length} and ${pileB.length}`, { kind: "effect" });
  const choosers = effect.players != null ? playersOf(resolution, effect) : [resolution.controller];
  const chooser = choosers[0] ?? resolution.controller;
  const pickAgent = agentFor(game, chooser) as { choosePile?(g: Game, p: PlayerId, a: ObjectId[], b: ObjectId[]): number } | null;
  let chosen = pickAgent?.choosePile?.(game, chooser, pileA.slice(), pileB.slice());
  if (chosen !== 0 && chosen !== 1) {
    const sum = (pile: ObjectId[]) => pile.reduce((t, id) => t + worth(id), 0);
    chosen = sum(pileA) >= sum(pileB) ? 0 : 1;
  }
  const [picked, rest] = chosen === 0 ? [pileA, pileB] : [pileB, pileA];
  resolution.remembered = picked.slice();
  execute(resolution, list(effect.children));
  resolution.remembered = rest.slice();
  execute(resolution, list(effect.otherwise));
});

/** CR 702.171a, c: saddled until end of turn, by the creatures tapped to pay. */
registerExecutor(ir("EffectKind.BECOME_SADDLED"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone !== Zone.BATTLEFIELD) continue;
    obj.saddledTurn = game.turn;
    obj.saddledBy = obj.costPaidObjects.slice();
    logRecord(game, `#${obj.id} becomes saddled`, { kind: "designation" });
    emit(game, event(EventKind.BECAME_SADDLED, { objectId: obj.id, player: obj.controller }));
  }
});

/** CR 702.112b: renowned until it leaves the battlefield. */
registerExecutor(ir("EffectKind.BECOME_RENOWNED"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone === Zone.BATTLEFIELD && !obj.renowned) {
      obj.renowned = true;
      invalidateAllCharacteristics(resolution.game);
      logRecord(resolution.game, `#${obj.id} becomes renowned`, { kind: "designation" });
      emit(resolution.game, event(EventKind.BECAME_RENOWNED, { objectId: obj.id, player: obj.controller, source: resolution.source }));
    }
  }
});

/** CR 702.55a-b: exile the haunt card from the graveyard, haunting the target. */
registerExecutor(ir("EffectKind.HAUNT"), (resolution, effect) => {
  const game = resolution.game;
  const haunted = objectsOf(resolution, effect);
  let card = game.objects.get(resolution.source);
  if (card !== undefined && !isLive(card)) card = game.objects.get(card.supersededBy);
  if (card === undefined || !isLive(card) || card.zone !== Zone.GRAVEYARD) return;
  const exiled = moveObject(game, card, Zone.EXILE, { toPlayer: card.owner });
  if (haunted.length > 0) {
    exiled.haunting = haunted[0]!.id;
    logRecord(game, `#${exiled.id} haunts #${haunted[0]!.id}`, { kind: "effect" });
  }
});

registerExecutor(ir("EffectKind.TAKE_INITIATIVE"), (resolution, effect) => {
  for (const pid of playersOf(resolution, effect)) takeInitiative(resolution.game, pid);
});

// PORT: TURN_FACE_DOWN, MANIFEST, RING_TEMPTS and
// SACRIFICE_BLOCKERS_AT_END_OF_COMBAT call the parser's CR 700 modules
// (cr708, cr701_ring), not published yet; BLOCKS_SOURCE_IF_ABLE needs
// the requirements half of restrictions (comes with combat). Logged and
// skipped until then.

// -- choices, modes, designations, casting during a resolution, turn changes ----------------------

/** CR 701.55: each facing player, in APNAP order, picks an option and all of it happens. */
registerExecutor(ir("EffectKind.VILLAINOUS_CHOICE"), (resolution, effect) => {
  const game = resolution.game;
  const options = list(effect.children);
  if (options.length === 0) return;
  const facing = new Set(playersOf(resolution, effect));
  const previous = resolution.thatPlayer;
  for (const pid of apnapOrder(game).filter((p) => facing.has(p))) {
    const agent = agentFor(game, pid) as { chooseVillainousOption?(g: Game, p: PlayerId, o: readonly Effect[]): number } | null;
    const picked = agent?.chooseVillainousOption?.(game, pid, options);
    const index = typeof picked === "number" && picked >= 0 && picked < options.length ? picked : 0;
    logRecord(game, `${player(game, pid).name} chooses: ${options[index]!.text || index}`, { kind: "choice", player: pid });
    resolution.thatPlayer = pid;
    executeOne(resolution, options[index]!);
  }
  resolution.thatPlayer = previous;
});

/** CR 700.2: only the modes chosen on announcement (a mana ability's ride on the resolution, CR 605.3b). */
registerExecutor(ir("EffectKind.CHOOSE_MODE"), (resolution, effect) => {
  const chosen = resolution.stackObject !== null ? resolution.stackObject.chosenModes : resolution.chosenModes;
  const children = list(effect.children);
  for (const index of chosen) if (index >= 0 && index < children.length) executeOne(resolution, children[index]!);
});

/** CR 716.2a: "this Class's level becomes N", announced to its watchers (CR 716.2). */
registerExecutor(ir("EffectKind.SET_CLASS_LEVEL"), (resolution, effect) => {
  const amount = amountOf(resolution, effect);
  for (const obj of objectsOf(resolution, effect)) {
    if (classLevel(resolution.game, obj.id) === amount) continue;
    setClassLevel(resolution.game, obj.id, amount);
    logRecord(resolution.game, `#${obj.id} becomes level ${amount}`, { kind: "class-level", player: obj.controller });
    emit(resolution.game, event(EventKind.CLASS_LEVEL_GAINED, { objectId: obj.id, player: obj.controller, amount }));
  }
});

registerExecutor(ir("EffectKind.BECOME_SOLVED"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) becomeSolved(resolution.game, obj.id);
});

registerExecutor(ir("EffectKind.FLIP_PERMANENT"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) flip(resolution.game, obj);
});

/**
 * CR 115.7: change the targets of something on the stack; each new target
 * legal and not one already chosen (CR 115.3), else the old one stays. A
 * spell's new targets are its controller's choice, as a copy's are.
 */
registerExecutor(ir("EffectKind.CHANGE_TARGETS"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone !== Zone.STACK) continue;
    if (obj.ability === null) {
      chooseNewTargets(game, obj, resolution.controller);
      continue;
    }
    const nodes = targetedNodes(list(obj.ability.effects), obj.chosenModes.length > 0 ? obj.chosenModes : null).filter((n) => n.targets != null);
    const rebuilt: number[][] = [];
    let changed = false;
    nodes.forEach((node, index) => {
      const current = index < obj.targets.length ? obj.targets[index]! : [];
      const legal = matchHooks().find(game, node.targets!, { source: obj.id, controller: obj.controller }).map((c) => c.id).filter((id) => !current.includes(id));
      if (legal.length === 0) {
        rebuilt.push(current.slice());
        return;
      }
      rebuilt.push([legal[0]!]);
      changed = true;
    });
    if (changed) {
      obj.targets = rebuilt;
      logRecord(game, `Targets changed for #${obj.id}`, { kind: "effect" });
    }
  }
});

/** CR 118.5: cast without paying the mana cost (cascade, suspend, rebound): still casting. */
registerExecutor(ir("EffectKind.CAST_WITHOUT_PAYING"), (resolution, effect) => {
  const game = resolution.game;
  if (flag(effect.cast_a_copy)) {
    castACopyWithoutPaying(resolution, effect);
    return;
  }
  for (const obj of objectsOf(resolution, effect)) {
    if (obj.zone === Zone.BATTLEFIELD || obj.zone === Zone.STACK) continue;
    if (!wants(resolution, effect)) continue;
    obj.castWithoutPaying = true;
    try {
      castSpell(game, resolution.controller, action(ActionKind.CAST_SPELL, { source: obj.id, faceIndex: num(effect.face_index) }));
    } catch (e) {
      if (!(e instanceof CastError)) throw e;
      obj.castWithoutPaying = false;
      logRecord(game, `Free cast abandoned: ${e.message}`, { kind: "illegal" });
    }
  }
});

/** CR 707.12: cast a copy of the object, owned by who casts it (CR 112.2a); an uncast copy is left for CR 704.5e. */
function castACopyWithoutPaying(resolution: Resolution, effect: Effect): void {
  const game = resolution.game;
  const spec = effect.targets;
  let originals: GameObject[];
  if (spec == null || flag(spec.source_only)) {
    const original = game.objects.get(resolution.source);
    originals = original !== undefined && original.card != null ? [original] : [];
  } else {
    originals = objectsOf(resolution, effect);
  }
  for (const original of originals) {
    const zone = effect.zone != null ? (num(effect.zone) as Zone) : original.zone;
    const copy = createObject(game, original.card, resolution.controller, zone, { kind: ObjectKind.COPY, faceIndex: original.faceIndex });
    if (!wants(resolution, effect)) continue;
    copy.castWithoutPaying = true;
    try {
      castSpell(game, resolution.controller, action(ActionKind.CAST_SPELL, { source: copy.id, faceIndex: num(effect.face_index) }));
    } catch (e) {
      if (!(e instanceof CastError)) throw e;
      logRecord(game, `Free cast of a copy abandoned: ${e.message}`, { kind: "illegal" });
    }
  }
}

/** CR 702.85a: against this spell's mana value as it last existed. */
registerExecutor(ir("EffectKind.CASCADE"), (resolution) => {
  const spell = resolution.game.objects.get(resolution.source);
  if (spell === undefined) return;
  const below = charsManaValue(characteristicsOf(resolution.game, spell));
  exileUntilAFreeCast(resolution, (mv) => mv < below, false);
});

/** CR 701.57a: discover N. */
registerExecutor(ir("EffectKind.DISCOVER"), (resolution, effect) => {
  const limit = amountOf(resolution, effect);
  exileUntilAFreeCast(resolution, (mv) => mv <= limit, true);
});

/**
 * Cascade and discover: exile from the top until a nonland card that fits;
 * it may be cast free (discover: else to hand); the rest go to the bottom in
 * a random order (the game's generator).
 */
function exileUntilAFreeCast(resolution: Resolution, fits: (manaValue: number) => boolean, toHandIfNotCast: boolean): void {
  const game = resolution.game;
  const pid = resolution.controller;
  const library = player(game, pid).library;
  const exiled: GameObject[] = [];
  let hit: GameObject | null = null;
  while (library.length > 0) {
    const card = game.objects.get(library[0]!)!;
    const moved = moveObject(game, card, Zone.EXILE, { toPlayer: card.owner });
    const chars = characteristicsOf(game, moved);
    if (!isLand(chars) && fits(charsManaValue(chars))) {
      hit = moved;
      break;
    }
    exiled.push(moved);
  }
  if (hit !== null) {
    let cast = false;
    if (wants(resolution, { $: "Effect", kind: ir("EffectKind.CAST_WITHOUT_PAYING"), text: "cast it free" })) {
      hit.castWithoutPaying = true;
      try {
        castSpell(game, pid, action(ActionKind.CAST_SPELL, { source: hit.id }));
        cast = true;
      } catch (e) {
        if (!(e instanceof CastError)) throw e;
        hit.castWithoutPaying = false;
        logRecord(game, `Free cast abandoned: ${e.message}`, { kind: "illegal" });
      }
    }
    if (!cast) {
      if (toHandIfNotCast) moveObject(game, hit, Zone.HAND, { toPlayer: hit.owner });
      else exiled.push(hit);
    }
  }
  shuffle(game.rng, exiled);
  for (const obj of exiled) if (isLive(obj) && obj.zone === Zone.EXILE) moveObject(game, obj, Zone.LIBRARY, { toPlayer: obj.owner });
}

/** CR 601.3: play from elsewhere; a land uses the land drop (CR 305.2), a spell pays its cost. */
registerExecutor(ir("EffectKind.PLAY_FROM_ZONE"), (resolution, effect) => {
  const game = resolution.game;
  for (const obj of objectsOf(resolution, effect)) {
    if (!wants(resolution, effect)) continue;
    const p = player(game, resolution.controller);
    try {
      if (isLand(characteristicsOf(game, obj))) {
        if (p.landsPlayed >= p.maxLands) continue;
        playLand(game, resolution.controller, action(ActionKind.PLAY_LAND, { source: obj.id }));
      } else {
        castSpell(game, resolution.controller, action(ActionKind.CAST_SPELL, { source: obj.id, faceIndex: num(effect.face_index) }));
      }
    } catch (e) {
      if (!(e instanceof CastError)) throw e;
      logRecord(game, `Play abandoned: ${e.message}`, { kind: "illegal" });
    }
  }
});

/** CR 500.8: an extra phase after this one (the amount names it; main by default). */
registerExecutor(ir("EffectKind.EXTRA_PHASE"), (resolution, effect) => {
  const c = constantOf(effect.amount ?? { $: "Value" });
  const phase = (c ? c : Phase.PRECOMBAT_MAIN) as Phase;
  resolution.game.extraPhases.push(phase);
  logRecord(resolution.game, `Extra phase ${phase}`, { kind: "effect" });
});

/** CR 500.8: an extra step after this one. */
registerExecutor(ir("EffectKind.EXTRA_STEP"), (resolution, effect) => {
  const c = constantOf(effect.amount ?? { $: "Value" });
  const step = (c ? c : Step.MAIN) as Step;
  resolution.game.extraSteps.push(step);
  logRecord(resolution.game, `Extra step ${step}`, { kind: "effect" });
});

/** Skip the whole untap step, its turn-based actions included. */
registerExecutor(ir("EffectKind.UNTAP_STEP_SKIP"), (resolution) => {
  resolution.game.skippedSteps.add(Step.UNTAP);
});

/**
 * CR 701.34: each player votes, starting with the player after the
 * controller in turn order; the tally is kept; the winner's child happens.
 * KNOWN: F61 - Python calls `game.apnap_order(next_player(...))`, but
 * apnap_order takes no argument, so every vote crashes there; guarded here
 * with the evident intent, turn order from the next player.
 */
registerExecutor(ir("EffectKind.VOTE"), (resolution, effect) => {
  const game = resolution.game;
  const children = list(effect.children);
  const choices = Array.from({ length: Math.max(2, children.length) }, (_, i) => i);
  const tally = new Map(choices.map((c) => [c, 0]));
  for (const pid of turnOrderFrom(game, nextPlayer(game, resolution.controller))) {
    const agent = agentFor(game, pid) as { chooseVote?(g: Game, p: PlayerId, c: readonly number[]): number } | null;
    let pick = agent?.chooseVote?.(game, pid, choices) ?? choices[0]!;
    if (!tally.has(pick)) pick = choices[0]!;
    tally.set(pick, tally.get(pick)! + 1);
  }
  game.lastVote = tally;
  let winner = choices[0]!;
  for (const c of choices) if (tally.get(c)! > tally.get(winner)!) winner = c;
  if (winner >= 0 && winner < children.length) executeOne(resolution, children[winner]!);
});

/** How many times the resolving object's `keyword` cost was paid (CR 608.2h: as it last existed). */
function timesPaidFor(resolution: Resolution, keyword: string): number {
  let obj: GameObject | undefined | null = resolution.stackObject;
  if (obj === null || obj.ability !== null) obj = resolution.game.objects.get(resolution.source);
  return obj == null ? 0 : timesPaid(resolution.game, obj, keyword);
}

/** CR 702.33d, 702.157a: the children once per payment of the cost named first. */
registerExecutor(ir("EffectKind.REPEAT_PER_PAYMENT"), (resolution, effect) => {
  const keyword = list(effect.keywords)[0];
  if (keyword === undefined) return;
  for (let i = timesPaidFor(resolution, keyword); i > 0; i -= 1) execute(resolution, list(effect.children));
});

/** CR 702.174b: the promised gift, with that opponent as "that player"; CR 702.174c's event. */
registerExecutor(ir("EffectKind.GIVE_GIFT"), (resolution, effect) => {
  const game = resolution.game;
  let obj: GameObject | undefined | null = resolution.stackObject;
  if (obj === null || obj.ability !== null) obj = game.objects.get(resolution.source);
  if (obj == null || obj.chosenPlayer === NO_PLAYER) return;
  const recipient = obj.chosenPlayer;
  if (player(game, recipient).hasLost) return;
  const before = resolution.thatPlayer;
  resolution.thatPlayer = recipient;
  try {
    execute(resolution, list(effect.children));
  } finally {
    resolution.thatPlayer = before;
  }
  emit(game, event(EventKind.GIFT_GIVEN, { objectId: obj.id, player: resolution.controller, data: [recipient] }));
});

// -- players, the game ---------------------------------------------------------------------------

/** CR 723.1: control another player during their next turn; of yourself, nothing (CR 723.9). */
registerExecutor(ir("EffectKind.CONTROL_PLAYER"), (resolution, effect) => {
  const game = resolution.game;
  for (const pid of playersOf(resolution, effect)) {
    if (pid === resolution.controller) {
      releasePlayer(game, pid);
      continue;
    }
    controlPlayer(game, pid, resolution.controller);
  }
});

/** CR 727.1: the game ends with no winner or loser; the controller starts the new one (CR 727.1a). */
registerExecutor(ir("EffectKind.RESTART_GAME"), (resolution) => {
  const game = resolution.game;
  game.restartRequested = true;
  game.restartStartingPlayer = resolution.controller;
  logRecord(game, "The game will restart; no player wins or loses this one", { kind: "restart", player: resolution.controller });
});

/** CR 722.3a, c: a permanent with a prepare spell becomes prepared. */
registerExecutor(ir("EffectKind.BECOME_PREPARED"), (resolution, effect) => {
  for (const obj of objectsOf(resolution, effect)) becomePrepared(resolution.game, obj);
});

// -- choosing a quality (CR 614.1b) ---------------------------------------------------------------

/**
 * Choose a creature type, card name or colour, recorded on the permanent
 * that asked. A colour the payment planner needed is the one chosen (CR
 * 605.3b). With no agent: the commonest among what the chooser has.
 */
registerExecutor(ir("EffectKind.CHOOSE_QUALITY"), (resolution, effect) => {
  const game = resolution.game;
  const obj = game.objects.get(resolution.source);
  if (obj === undefined) return;
  const kind = list(effect.keywords)[0] ?? "creature type";
  const wanted = resolution.manaColor;
  if (kind === "color" && wanted && singleColors(wanted).length === 1) {
    obj.chosenColor = wanted;
    invalidateAllCharacteristics(game);
    return;
  }
  const agent = agentFor(game, resolution.controller) as { chooseQuality?(g: Game, p: PlayerId, kind: string): unknown } | null;
  if (agent?.chooseQuality !== undefined) {
    const picked = agent.chooseQuality(game, resolution.controller, kind);
    if (picked != null) {
      if (kind === "color") obj.chosenColor = Number(picked);
      else if (kind === "card name") obj.chosenName = String(picked);
      else obj.chosenType = String(picked);
      return;
    }
  }
  if (kind === "color") obj.chosenColor = commonestColour(game, resolution.controller);
  else if (kind === "card name") obj.chosenName = commonestOpposingName(game, resolution.controller); // CR 201.4 stand-in
  else obj.chosenType = commonestSubtype(game, resolution.controller);
  invalidateAllCharacteristics(game);
});

const byCountThenName = (a: [string, number], b: [string, number]): number => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);

/** The name an opponent has most of on the battlefield; ties by name. */
function commonestOpposingName(game: Game, controller: PlayerId): string {
  const seen = new Map<string, number>();
  for (const opponent of game.players.filter((p) => p.id !== controller && !p.hasLost)) {
    for (const obj of permanents(game, opponent.id)) {
      const name = characteristicsOf(game, obj).name;
      if (name) seen.set(name, (seen.get(name) ?? 0) + 1);
    }
  }
  return [...seen].sort(byCountThenName)[0]?.[0] ?? "";
}

/** The subtype this player has most of, on the battlefield and in hand; ties by name. */
function commonestSubtype(game: Game, pid: PlayerId): string {
  const tally = new Map<string, number>();
  for (const zone of [game.battlefield, player(game, pid).hand]) {
    for (const id of zone) {
      const obj = game.objects.get(id);
      if (obj === undefined || obj.controller !== pid) continue;
      for (const subtype of characteristicsOf(game, obj).typeLine.subtypes) tally.set(subtype, (tally.get(subtype) ?? 0) + 1);
    }
  }
  return [...tally].sort(byCountThenName)[0]?.[0] ?? "";
}

/** The colour this player has most of among their permanents; ties to the lowest bit. */
function commonestColour(game: Game, pid: PlayerId): number {
  const tally = new Map<number, number>();
  for (const id of game.battlefield) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.controller !== pid) continue;
    const colors = characteristicsOf(game, obj).colors as number;
    for (const bit of [1, 2, 4, 8, 16]) if (colors & bit) tally.set(bit, (tally.get(bit) ?? 0) + 1);
  }
  if (tally.size === 0) return 0;
  const best = Math.max(...tally.values());
  return Math.min(...[...tally].filter(([, c]) => c === best).map(([b]) => b));
}
