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
import type { Effect } from "../../ir";
import { Color, Duration, Supertype } from "../../kernel/enums";
import { event, EventKind, type GameEvent } from "../../kernel/events";
import { logRecord, player, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { isPlayerTarget, NO_OBJECT, NO_PLAYER, targetPlayer, type ObjectId, type PlayerId } from "../../kernel/ids";
import { hasSupertype } from "../cr200_parts_of_a_card/characteristics";
import { Act } from "../cr500_turn_structure/restrictions";
import { commanderColorIdentity } from "../cr903_commander/color_identity";
import { emit } from "../emit";
import { flag, ir, list, num, str } from "../ir_read";
import { parseManaSymbol } from "../mana/cost";
import { addMana, manaKind, manaKindKey, poolSnapshot, type ManaKind, type ManaRestriction } from "../mana/pool";
import { constantOf, isTargeted, targetsAPlayer } from "./effect_props";
import { characteristicsOf } from "./layers";
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
  const executor = EXECUTORS.get(num(effect.kind));
  if (executor === undefined) {
    // No executor: UNPARSED, or a kind not implemented. It does nothing, loudly.
    logRecord(resolution.game, `Effect not executed (${num(effect.kind)}): ${str(effect.text) || "effect"}`, {
      kind: "unimplemented",
      player: resolution.controller,
    });
    return;
  }
  // PORT: the _REMEMBERING bookkeeping (what "it" names next) comes with `_objects`.
  executor(resolution, effect);
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
