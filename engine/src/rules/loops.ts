/**
 * Infinite loops: noticed, shortcut, and ended (CR 732.2a, CR 104.4b).
 * Ported from mtgfish/rules/kernel/loops.py.
 *
 * Without this an infinite could not do anything: an optional combo (an
 * untap engine, a sacrifice-and-return loop) was cut off by the bot's cap on
 * activations per turn, and a mandatory one (Sanguine Bond and Exquisite
 * Blood) resolved trigger after trigger until the priority loop's round cap
 * threw the game out as a runaway. CR 732.2a lets the player running an
 * optional loop propose a number of iterations and skip to the result; CR
 * 104.4b says a loop of mandatory actions that nothing can stop is a draw.
 *
 * **Noticing.** Every action taken and every stack object resolved in a
 * priority window is reduced to a short step signature. When the same cycle
 * of steps repeats `LOOP_REPETITIONS` times in a row, the board is the same
 * shape at the start of each cycle, and each cycle changes the game by the
 * same amounts, it is a loop.
 *
 * **Not misfiring.** The shape includes the stack and the pending triggers,
 * so a chain draining a finite stack of triggers (Blood Artist watching a
 * board wipe) changes shape every cycle and is never a loop; and anything a
 * cycle consumes caps the shortcut at what is actually there.
 *
 * **Doing it.** The per-cycle change is scaled directly, with triggers
 * suppressed (each trigger that fires once per cycle is already in the
 * change). Optional loops run far enough to kill every opponent they can
 * reach, never far enough to kill their controller, never past their fuel;
 * resource-only loops to a generous cap; a loop that changes nothing is
 * stopped and recorded. A mandatory loop runs until the first player loses
 * or its fuel runs out; if neither ever happens, the game is a draw.
 *
 * Keys and steps are JSON arrays as strings (Python uses tuples). Where
 * Python iterated a set union of keys (an arbitrary order), the earlier
 * snapshot's keys come first, then the new ones, so the scaled moves happen
 * in a fixed order.
 */

import type { Effect, Value } from "../ir";
import { Zone } from "../kernel/enums";
import {
  invalidateAllCharacteristics,
  livingPlayers,
  logRecord,
  permanents,
  player,
  zoneList,
  createObject,
  type ContinuousEffect,
  type Game,
} from "../kernel/game";
import { addCounters, isToken, type GameObject } from "../kernel/gameobject";
import { allocateTimestamp, isPlayerTarget, targetPlayer, type PlayerId } from "../kernel/ids";
import { gainLife, loseLife, type Player } from "./cr100_game_concepts/player";
import { addMana, manaKindKey, removeMana, type ManaKind } from "./cr100_game_concepts/mana_pool";
import { actionKindName, type Action } from "./cr100_game_concepts/priority";
import { ir, list, num } from "./ir_read";
import { moveObject } from "./zones";

/** How many identical cycles make a loop. */
export const LOOP_REPETITIONS = 8;
/** The longest cycle, in steps, that is looked for. */
export const MAX_PERIOD = 12;

export const MANA_CAP = 1000;
export const COUNTER_CAP = 1000;
export const LIFE_CAP = 1000;
export const PUMP_CAP = 1000;
export const ZONE_CAP = 1000;
/** Tokens are real objects every board computation walks, so this one is small. */
export const TOKEN_CAP = 100;
/** A ceiling on any single shortcut, whatever the arithmetic says. */
export const MAX_SHORTCUT = 100_000;

const INF = Number.POSITIVE_INFINITY;

/** What a cycle can run out of; only the looping player's own resources bound a loop. */
const CONSUMABLE = new Set(["library", "hand", "graveyard", "exile", "mana", "counters", "tokens"]);
/** Bookkeeping any activity changes; scaled, never a goal. */
const DERIVED = new Set(["turn", "spells_cast"]);

const GROWTH_CAPS: Record<string, number> = {
  life: LIFE_CAP,
  energy: LIFE_CAP,
  experience: LIFE_CAP,
  mana: MANA_CAP,
  counters: COUNTER_CAP,
  tokens: TOKEN_CAP,
  pump: PUMP_CAP,
  hand: ZONE_CAP,
  graveyard: ZONE_CAP,
  exile: ZONE_CAP,
};

const ZONE_KEYS: readonly (readonly [string, Zone])[] = [
  ["library", Zone.LIBRARY],
  ["hand", Zone.HAND],
  ["graveyard", Zone.GRAVEYARD],
  ["exile", Zone.EXILE],
];

export const WINS = "far enough to finish the opponents it can reach";
export const RAN_OUT = "until it ran out of what it spends";
export const DECLINED = "finishing it would kill the player running it";
export const NOTHING = "it changed nothing";
export const RESOURCES = "as far as the resources are worth";
export const NOBODY = "a mandatory loop nothing can stop";
export const LOST = "until a player lost";
export const EMPTY = "it has nothing left to spend";

/** A step: a JSON array, ["A", player, kind, name, abilityIndex, x, targets] or ["R", controller, label, targets]. */
export type LoopStep = string;
type Key = readonly (string | number)[];
/** Keyed by the JSON of the key; the player a resource belongs to is always the second element. */
export type Values = Map<string, number>;

const keyOf = (...parts: (string | number)[]): string => JSON.stringify(parts);
const parse = (key: string): Key => JSON.parse(key) as Key;

// ---------------------------------------------------------------------------
// The window
// ---------------------------------------------------------------------------

interface Snapshot {
  readonly shape: string;
  readonly values: Values;
  /** The newest continuous effect's timestamp, to tell one cycle's effects apart. */
  readonly mark: number;
}

interface LoopWindow {
  history: string[];
  /** `runs[p]`: consecutive steps equal to the step `p` before them. */
  runs: number[];
  snapshots: Map<number, Snapshot[]>;
}

const newWindow = (): LoopWindow => ({ history: [], runs: new Array(MAX_PERIOD + 1).fill(0), snapshots: new Map() });

/** A fresh window. Loops are looked for inside one priority window only. */
export function beginWindow(game: Game): void {
  game.loopWindow = newWindow();
}

/** One action as a step, read before it is performed (a sacrifice cost can move the source). */
export function actionStep(game: Game, playerId: PlayerId, act: Action): LoopStep {
  const obj = game.objects.get(act.source);
  return JSON.stringify(["A", playerId, actionKindName(act.kind), nameOf(obj), act.abilityIndex, act.xValue, targetsOf(game, act.targets)]);
}

/** One resolving stack object as a step, read before it resolves. */
export function resolutionStep(game: Game, obj: GameObject | null | undefined): LoopStep {
  if (obj === null || obj === undefined) return JSON.stringify(["R", -1, "", []]);
  return JSON.stringify(["R", obj.controller, labelOf(obj), targetsOf(game, obj.targets)]);
}

/** Record a step, and shortcut the loop it completes; true when something was done (the game may be over). */
export function noteStep(game: Game, step: LoopStep | null): boolean {
  const window = game.loopWindow as LoopWindow | null;
  if (step === null || window === null || window === undefined || game.gameOver) return false;

  const history = window.history;
  history.push(step);
  if (history.length > MAX_PERIOD + 1) history.shift();
  const size = history.length;

  let fundamental = 0;
  for (let period = 1; period <= MAX_PERIOD; period += 1) {
    if (size > period && history[size - 1] === history[size - 1 - period]) {
      window.runs[period] = (window.runs[period] ?? 0) + 1;
    } else if (window.runs[period]) {
      window.runs[period] = 0;
      window.snapshots.delete(period);
    }
    // The shortest period that has repeated at least once.
    if (!fundamental && (window.runs[period] ?? 0) >= period) fundamental = period;
  }
  if (!fundamental) return false;
  const run = window.runs[fundamental] ?? 0;
  if (run % fundamental) return false; // Mid-cycle.

  let snapshots = window.snapshots.get(fundamental);
  if (snapshots === undefined) window.snapshots.set(fundamental, (snapshots = []));
  snapshots.push(takeSnapshot(game));
  if (snapshots.length > 4) snapshots.shift();

  const cycles = Math.floor(run / fundamental) + 1;
  if (cycles < LOOP_REPETITIONS || snapshots.length < 3) return false;

  const cycle = history.slice(size - fundamental, size);
  return consider(game, cycle, snapshots);
}

/** Whether this action belongs to a loop already shortcut this turn. */
export function isExhausted(game: Game, playerId: PlayerId, act: Action): boolean {
  if (game.exhaustedLoopSteps.size === 0) return false;
  return game.exhaustedLoopSteps.has(actionStep(game, playerId, act));
}

/** Whether this action belongs to a loop that won and may carry on against the next opponent. */
export function isContinuing(game: Game, playerId: PlayerId, act: Action): boolean {
  if (game.continuingLoopSteps.size === 0) return false;
  return game.continuingLoopSteps.has(actionStep(game, playerId, act));
}

// ---------------------------------------------------------------------------
// Deciding
// ---------------------------------------------------------------------------

/** A living player, as far as deciding a shortcut is concerned. */
export interface Contestant {
  readonly id: number;
  readonly life: number;
  readonly poison?: number;
  readonly library?: number;
  readonly cannotLose?: boolean;
}

/** How many more cycles to run, and why. */
export interface Decision {
  readonly cycles: number;
  readonly outcome: "shortcut" | "stopped" | "declined" | "draw" | "ends";
  readonly reason: string;
}

/** How far a loop is run. Pure. `delta` is what one cycle changes, `values` what there is now. */
export function decide(options: { optional: boolean; controller: number; delta: Values; values: Values; players: readonly Contestant[] }): Decision {
  const { optional, controller, delta, values, players } = options;
  const bound = consumableBound(delta, values, controller, optional);
  const deaths = deathsOf(delta, players);
  if (optional) return decideOptional(controller, delta, bound, deaths, players);
  return decideMandatory(bound, deaths);
}

function consumableBound(delta: Values, values: Values, controller: number, optional: boolean): number {
  let bound = INF;
  for (const [k, change] of delta) {
    const key = parse(k);
    if (change >= 0 || !CONSUMABLE.has(String(key[0])) || key[1] !== controller) continue;
    let available = values.get(k) ?? 0;
    // An optional loop that draws its controller's library leaves a card.
    if (optional && key[0] === "library" && (delta.get(keyOf("hand", controller)) ?? 0) > 0) available = Math.max(0, available - 1);
    bound = Math.min(bound, Math.floor(available / -change));
  }
  return bound;
}

function deathsOf(delta: Values, players: readonly Contestant[]): Map<number, number> {
  const deaths = new Map<number, number>();
  for (const p of players) {
    if (p.cannotLose) continue;
    let cycles = INF;
    const life = delta.get(keyOf("life", p.id)) ?? 0;
    if (life < 0) cycles = Math.min(cycles, Math.ceil(Math.max(0, p.life) / -life));
    const poison = delta.get(keyOf("poison", p.id)) ?? 0;
    if (poison > 0) cycles = Math.min(cycles, Math.ceil(Math.max(0, 10 - (p.poison ?? 0)) / poison));
    // CR 704.5b: drawing from an empty library, one cycle after the last card.
    const library = delta.get(keyOf("library", p.id)) ?? 0;
    if (library < 0 && (delta.get(keyOf("hand", p.id)) ?? 0) > 0) cycles = Math.min(cycles, Math.floor((p.library ?? 0) / -library) + 1);
    deaths.set(p.id, cycles);
  }
  return deaths;
}

function decideOptional(controller: number, delta: Values, bound: number, deaths: Map<number, number>, players: readonly Contestant[]): Decision {
  const own = deaths.get(controller) ?? INF;
  const survive = own !== INF ? own - 1 : INF;
  const limit = Math.min(bound, survive);

  const goals: number[] = [];
  for (const [p, cycles] of deaths) if (p !== controller && cycles !== INF) goals.push(cycles);
  // Milling an opponent's library out is a win too, one draw step later.
  for (const p of players) {
    if (p.id === controller) continue;
    const change = delta.get(keyOf("library", p.id)) ?? 0;
    if (change < 0 && (delta.get(keyOf("hand", p.id)) ?? 0) <= 0) goals.push(Math.ceil((p.library ?? 0) / -change));
  }

  if (goals.length > 0) {
    const reachable = goals.filter((g) => g <= limit);
    if (reachable.length > 0) return { cycles: clamp(Math.max(...reachable)), outcome: "shortcut", reason: WINS };
    if (survive <= bound) return { cycles: 0, outcome: "declined", reason: DECLINED };
    return { cycles: clamp(limit), outcome: "shortcut", reason: RAN_OUT };
  }

  const growth = growthCycles(delta);
  if (growth === null) return { cycles: 0, outcome: "stopped", reason: NOTHING };
  const cycles = Math.min(limit, growth);
  if (cycles <= 0) return { cycles: 0, outcome: "stopped", reason: EMPTY };
  return { cycles: clamp(cycles), outcome: "shortcut", reason: cycles === growth ? RESOURCES : RAN_OUT };
}

function decideMandatory(bound: number, deaths: Map<number, number>): Decision {
  const first = deaths.size > 0 ? Math.min(...deaths.values()) : INF;
  const cycles = Math.min(bound, first);
  if (cycles === INF) return { cycles: 0, outcome: "draw", reason: NOBODY }; // CR 104.4b
  if (cycles <= 0) return { cycles: 0, outcome: "ends", reason: EMPTY };
  return { cycles: clamp(cycles), outcome: "shortcut", reason: first <= bound ? LOST : RAN_OUT };
}

function growthCycles(delta: Values): number | null {
  const caps: number[] = [];
  for (const [k, change] of delta) {
    const kind = String(parse(k)[0]);
    if (change > 0 && kind in GROWTH_CAPS) caps.push(Math.max(1, Math.floor(GROWTH_CAPS[kind]! / change)));
  }
  return caps.length > 0 ? Math.min(...caps) : null;
}

const clamp = (cycles: number): number => Math.trunc(Math.min(cycles, MAX_SHORTCUT));

// ---------------------------------------------------------------------------
// Recognising
// ---------------------------------------------------------------------------

function consider(game: Game, cycle: readonly string[], snapshots: readonly Snapshot[]): boolean {
  const [first, second, third] = snapshots.slice(-3) as [Snapshot, Snapshot, Snapshot];
  if (!(first.shape === second.shape && second.shape === third.shape)) return false;

  const before = deltaOf(first.values, second.values);
  const latest = deltaOf(second.values, third.values);
  if (!sameValues(before, latest)) return false;

  const earlierEffects = cycleEffects(game, first.mark, second.mark);
  const latestEffects = cycleEffects(game, second.mark, third.mark);
  if (effectSignature(earlierEffects) !== effectSignature(latestEffects)) return false;

  return resolveLoop(game, cycle, third.values, latest, latestEffects);
}

type Pump = readonly [template: ContinuousEffect, count: number, power: number, toughness: number];

function resolveLoop(game: Game, cycle: readonly string[], values: Values, delta: Values, effects: readonly ContinuousEffect[]): boolean {
  const steps = cycle.map((s) => JSON.parse(s) as unknown[]);
  const optional = steps.some((s) => s[0] === "A");
  const controller = controllerOf(game, steps);
  const pumps = pumpGroups(effects);

  let decisionDelta: Values = new Map(delta);
  pumps.forEach(([template, count, power, toughness], index) => {
    const size = Math.max(Math.abs(power), Math.abs(toughness)) * count;
    if (size) decisionDelta.set(keyOf("pump", template.controller, index), size);
  });
  decisionDelta = new Map([...decisionDelta].filter(([k]) => !DERIVED.has(String(parse(k)[0]))));

  const decision = decide({ optional, controller, delta: decisionDelta, values, players: contestants(game) });
  const description = describe(steps);
  // Whatever happens, finding the next loop needs fresh cycles.
  game.loopWindow = newWindow();
  const actions = cycle.filter((s, i) => steps[i]![0] === "A");

  if (decision.outcome === "draw") {
    drawGame(game, description);
    return true;
  }
  if (decision.cycles <= 0) {
    if (!optional) return false; // It ends on its own at the next cycle.
    for (const a of actions) game.exhaustedLoopSteps.add(a);
    note(game, decision.outcome, description, 0, decision.reason);
    return true;
  }

  const victim = aimedAt(steps, controller, decisionDelta);
  if (victim !== null && (decision.reason === WINS || decision.reason === LOST)) {
    const total = runReaimed(game, delta, pumps, victim, controller, optional);
    note(game, "shortcut", description, total, `${decision.reason}, one target at a time`);
  } else {
    applyCycles(game, delta, pumps, decision.cycles);
    note(game, "shortcut", description, decision.cycles, decision.reason);
  }
  if (optional) {
    // Killing one opponent re-aims the loop at the next: it may carry on.
    const into = decision.reason === WINS ? game.continuingLoopSteps : game.exhaustedLoopSteps;
    for (const a of actions) into.add(a);
  }
  return true;
}

function contestants(game: Game): Contestant[] {
  return livingPlayers(game).map((p) => ({ id: p.id, life: p.life, poison: p.poison, library: p.library.length, cannotLose: p.cannotLose }));
}

/** What a loop can do to a player that it would do to another just the same if aimed at them. */
const VICTIM_KEYS = new Set(["life", "poison", "library", "hand", "graveyard", "exile"]);

/** The one opponent a loop is pointed at, when pointing it is a choice ("target opponent", chosen each time). */
function aimedAt(steps: readonly unknown[][], controller: number, delta: Values): number | null {
  const aimed = new Set<number>();
  for (const step of steps) for (const p of playerTargets(step[step.length - 1])) aimed.add(p);
  if (aimed.size !== 1) return null;
  const victim = [...aimed][0]!;
  if (victim === controller) return null;
  let hit = false;
  for (const k of delta.keys()) {
    const key = parse(k);
    if (key.length < 2 || key[1] === controller) continue;
    if (key[1] !== victim || !VICTIM_KEYS.has(String(key[0]))) return null;
    hit = true;
  }
  return hit ? victim : null;
}

function playerTargets(targets: unknown): number[] {
  const found: number[] = [];
  if (!Array.isArray(targets)) return found;
  for (const item of targets) {
    if (!Array.isArray(item)) continue;
    if (item.length === 2 && item[0] === "p") found.push(item[1] as number);
    else found.push(...playerTargets(item));
  }
  return found;
}

function reaim(delta: Values, source: number, target: number): Values {
  if (source === target) return new Map(delta);
  const out: Values = new Map();
  for (const [k, change] of delta) {
    let key = k;
    const parts = parse(k);
    if (VICTIM_KEYS.has(String(parts[0])) && parts.length >= 2 && parts[1] === source) key = JSON.stringify([parts[0], target, ...parts.slice(2)]);
    out.set(key, (out.get(key) ?? 0) + change);
  }
  return out;
}

/** A loop aimed at one opponent, run to lethal on each opponent in turn, each decided afresh. */
function runReaimed(game: Game, delta: Values, pumps: readonly Pump[], victim: number, controller: number, optional: boolean): number {
  const order = [victim, ...livingPlayers(game).map((p) => p.id).filter((id) => id !== controller && id !== victim)];
  let total = 0;
  for (const target of order) {
    const aimed = reaim(delta, victim, target);
    const decision = decide({
      optional,
      controller,
      delta: new Map([...aimed].filter(([k]) => !DERIVED.has(String(parse(k)[0])))),
      values: takeSnapshot(game).values,
      players: contestants(game),
    });
    if (decision.cycles <= 0) break;
    applyCycles(game, aimed, pumps, decision.cycles);
    total += decision.cycles;
    if ((decision.reason !== WINS && decision.reason !== LOST) || player(game, controller).life <= 0) break;
  }
  return total;
}

function controllerOf(game: Game, steps: readonly unknown[][]): number {
  for (const step of steps) if (step[0] === "A") return step[1] as number;
  for (const step of steps) if (step[0] === "R" && (step[1] as number) >= 0) return step[1] as number;
  return game.activePlayer;
}

/** A loop, in words a report can show. */
export function describe(steps: readonly unknown[][]): string {
  const parts: string[] = [];
  for (const step of steps) {
    let label: string;
    if (step[0] === "A") {
      label = `${String(step[2]).toLowerCase().replace(/_/g, " ")} ${String(step[3])}`.trim();
    } else {
      const text = String(step[2]);
      label = `resolve ${text.length <= 60 ? text : `${text.slice(0, 57)}...`}`.trim();
    }
    if (!parts.includes(label)) parts.push(label);
  }
  return parts.join("; ");
}

// ---------------------------------------------------------------------------
// Measuring
// ---------------------------------------------------------------------------

/** Mana kinds seen in snapshots, by key, so a scaled change can add them back. */
const MANA_KINDS = new Map<string, ManaKind>();

/** The game at a cycle boundary: `shape` must not change, `values` may. */
function takeSnapshot(game: Game): Snapshot {
  const values: Values = new Map();
  const add = (key: string, amount: number): void => void values.set(key, (values.get(key) ?? 0) + amount);
  const living: number[] = [];
  for (const p of game.players) {
    if (p.hasLost) continue;
    const pid = p.id;
    living.push(pid);
    values.set(keyOf("life", pid), p.life);
    values.set(keyOf("poison", pid), p.poison);
    values.set(keyOf("energy", pid), p.energy);
    values.set(keyOf("experience", pid), p.experience);
    values.set(keyOf("library", pid), p.library.length);
    values.set(keyOf("hand", pid), p.hand.length);
    values.set(keyOf("graveyard", pid), p.graveyard.length);
    for (const bucket of p.manaPool.buckets.values()) {
      if (!bucket.count) continue;
      const kindKey = manaKindKey(bucket.kind);
      MANA_KINDS.set(kindKey, bucket.kind);
      values.set(keyOf("mana", pid, kindKey), bucket.count);
    }
  }
  for (const id of zoneList(game, Zone.EXILE)) {
    const obj = game.objects.get(id);
    if (obj !== undefined) add(keyOf("exile", obj.owner), 1);
  }
  const board: string[] = [];
  for (const obj of permanents(game)) {
    const name = nameOf(obj);
    if (isToken(obj)) add(keyOf("tokens", obj.controller, name), 1);
    else board.push(JSON.stringify([name, obj.controller, obj.tapped, obj.phasedOut]));
    for (const [kind, amount] of obj.counters) if (amount) values.set(keyOf("counters", obj.controller, obj.id, kind), amount);
  }
  for (const [key, amount] of game.turnHistory) values.set(keyOf("turn", key), amount);
  values.set(keyOf("spells_cast"), game.spellsCastThisTurn);

  const stack = game.stack.map((id) => labelOf(game.objects.get(id)));
  const shape = JSON.stringify([game.step, stack, game.pendingTriggers.length, board.sort(), living]);
  let mark = 0;
  for (const ce of game.continuousEffects) if (ce.timestamp > mark) mark = ce.timestamp;
  return { shape, values, mark };
}

/** What changed between two boundaries, non-zero entries only; a counter key on one side only is an object that came or went. */
function deltaOf(before: Values, after: Values): Values {
  const out: Values = new Map();
  const keys = [...before.keys(), ...[...after.keys()].filter((k) => !before.has(k))];
  for (const k of keys) {
    if (k.startsWith('["counters"') && (!before.has(k) || !after.has(k))) continue;
    const change = (after.get(k) ?? 0) - (before.get(k) ?? 0);
    if (change) out.set(k, change);
  }
  return out;
}

function sameValues(a: Values, b: Values): boolean {
  if (a.size !== b.size) return false;
  for (const [k, v] of a) if (b.get(k) !== v) return false;
  return true;
}

/** Continuous effects made by resolutions between two boundaries. */
function cycleEffects(game: Game, low: number, high: number): ContinuousEffect[] {
  return game.continuousEffects.filter((ce) => low < ce.timestamp && ce.timestamp <= high && !ce.fromStaticAbility && !ce.expired);
}

const MODIFY_PT = ir("EffectKind.MODIFY_PT");
const CONSTANT = ir("ValueKind.CONSTANT");

/** A Value's constant, if it is one (Python `Value.is_constant`). */
function constantOf(value: Value | null | undefined): number | null {
  if (value == null) return 0;
  if (num(value.kind, CONSTANT) !== CONSTANT) return null;
  return value.constant ?? 0;
}

function specificOf(effect: Effect): number[] {
  return [...list(effect.targets?.specific)].sort((a, b) => a - b);
}

/** The effects a cycle made, comparable between cycles. */
function effectSignature(effects: readonly ContinuousEffect[]): string {
  const signature = new Map<string, number>();
  for (const ce of effects) {
    const effect = ce.effect as Effect;
    const power = constantOf(effect.amount);
    const toughness = constantOf(effect.amount2);
    const key = num(effect.kind) === MODIFY_PT && power !== null && toughness !== null
      ? JSON.stringify(["pt", specificOf(effect), power, toughness, ce.duration])
      : JSON.stringify(["other", num(effect.kind), specificOf(effect)]);
    signature.set(key, (signature.get(key) ?? 0) + 1);
  }
  return JSON.stringify([...signature].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** Constant power/toughness changes a cycle made, grouped for scaling (anything else is the same after one or a thousand). */
function pumpGroups(effects: readonly ContinuousEffect[]): Pump[] {
  const groups = new Map<string, [ContinuousEffect, number]>();
  for (const ce of effects) {
    const effect = ce.effect as Effect;
    if (num(effect.kind) !== MODIFY_PT) continue;
    const power = constantOf(effect.amount);
    const toughness = constantOf(effect.amount2);
    if (power === null || toughness === null) continue;
    const key = JSON.stringify([specificOf(effect), power, toughness, ce.duration, ce.controller, ce.source]);
    const group = groups.get(key);
    if (group) group[1] += 1;
    else groups.set(key, [ce, 1]);
  }
  return [...groups.values()].map(([ce, count]) => {
    const effect = ce.effect as Effect;
    return [ce, count, constantOf(effect.amount)!, constantOf(effect.amount2)!] as const;
  });
}

// ---------------------------------------------------------------------------
// Applying
// ---------------------------------------------------------------------------

/** Scale one cycle's change by `cycles`, triggers off (each is already in the change). */
function applyCycles(game: Game, delta: Values, pumps: readonly Pump[], cycles: number): void {
  game.lastLoopTurn = game.turn;
  const savedHistory = new Map(game.turnHistory);
  game.suppressTriggers = true;
  try {
    applyPlayers(game, delta, cycles);
    applyZones(game, delta, cycles);
    applyCounters(game, delta, cycles);
    applyTokens(game, delta, cycles);
    applyPumps(game, pumps, cycles);
  } finally {
    game.suppressTriggers = false;
  }
  game.turnHistory.clear();
  for (const [k, v] of savedHistory) game.turnHistory.set(k, v);
  for (const [k, change] of delta) {
    const key = parse(k);
    if (key[0] === "turn") {
      const inner = String(key[1]);
      game.turnHistory.set(inner, (game.turnHistory.get(inner) ?? 0) + change * cycles);
    } else if (key[0] === "spells_cast") {
      game.spellsCastThisTurn += change * cycles;
    }
  }
  invalidateAllCharacteristics(game);
}

function applyPlayers(game: Game, delta: Values, cycles: number): void {
  for (const p of livingPlayers(game)) {
    const pid = p.id;
    const life = (delta.get(keyOf("life", pid)) ?? 0) * cycles;
    if (life > 0) gainLife(p, life);
    else if (life < 0) loseLife(p, -life);

    const poison = (delta.get(keyOf("poison", pid)) ?? 0) * cycles;
    if (poison > 0) p.poison += poison;

    for (const name of ["energy", "experience"] as const) {
      const change = (delta.get(keyOf(name, pid)) ?? 0) * cycles;
      if (change) p[name] = Math.max(0, p[name] + change);
    }

    for (const [k, change] of delta) {
      const key = parse(k);
      if (key[0] !== "mana" || key[1] !== pid) continue;
      const kind = MANA_KINDS.get(String(key[2]));
      if (kind === undefined) continue;
      const amount = change * cycles;
      if (amount > 0) {
        addMana(p.manaPool, kind, Math.min(amount, MANA_CAP));
      } else if (amount < 0) {
        const available = [...p.manaPool.buckets.values()].find((b) => manaKindKey(b.kind) === key[2])?.count ?? 0;
        if (available) removeMana(p.manaPool, kind, Math.min(-amount, available));
      }
    }
  }
}

/** Cards moved between one player's zones as flows; a draw past the library's end is a loss (CR 704.5b). */
function applyZones(game: Game, delta: Values, cycles: number): void {
  for (const p of livingPlayers(game)) {
    const flows = ZONE_KEYS.map(([name, zone]) => [zone, (delta.get(keyOf(name, p.id)) ?? 0) * cycles] as const);
    const sources = flows.filter(([, amount]) => amount < 0).map(([zone, amount]) => [zone, -amount] as [Zone, number]);
    for (const [sink, amount] of flows.filter(([, a]) => a > 0)) {
      let wanted = amount;
      for (const source of sources) {
        if (wanted <= 0) break;
        const take = Math.min(wanted, source[1]);
        if (take <= 0) continue;
        const moved = moveCards(game, p, source[0], sink, take);
        if (source[0] === Zone.LIBRARY && sink === Zone.HAND && moved < take) p.attemptedDrawFromEmptyLibrary = true;
        source[1] -= take;
        wanted -= take;
      }
    }
  }
}

function moveCards(game: Game, p: Player, source: Zone, sink: Zone, count: number): number {
  let pool: number[];
  if (source === Zone.EXILE) pool = zoneList(game, Zone.EXILE).filter((id) => game.objects.get(id)?.owner === p.id);
  else if (source === Zone.LIBRARY) pool = p.library.slice();
  else if (source === Zone.HAND) pool = p.hand.slice();
  else pool = p.graveyard.slice();
  let moved = 0;
  for (const id of pool.slice(0, count)) {
    const obj = game.objects.get(id);
    if (obj === undefined) continue;
    moveObject(game, obj, sink, { toPlayer: p.id });
    moved += 1;
    if (source === Zone.LIBRARY && sink === Zone.HAND) p.cardsDrawnThisTurn += 1;
  }
  return moved;
}

function applyCounters(game: Game, delta: Values, cycles: number): void {
  for (const [k, change] of delta) {
    const key = parse(k);
    if (key[0] !== "counters") continue;
    const obj = game.objects.get(key[2] as number);
    if (obj === undefined || obj.zone !== Zone.BATTLEFIELD) continue;
    const kind = String(key[3]);
    const amount = change * cycles;
    if (amount > 0) addCounters(obj, kind, Math.min(amount, COUNTER_CAP));
    else obj.counters.set(kind, Math.max(0, (obj.counters.get(kind) ?? 0) + amount));
  }
}

/** More of the tokens a cycle made (cloned from one of them), or fewer of the ones it spent. */
function applyTokens(game: Game, delta: Values, cycles: number): void {
  for (const [k, change] of delta) {
    const key = parse(k);
    if (key[0] !== "tokens") continue;
    const controller = key[1] as number;
    const name = String(key[2]);
    const amount = change * cycles;
    const existing = permanents(game, controller)
      .filter((obj) => isToken(obj) && nameOf(obj) === name)
      .sort((a, b) => a.id - b.id);
    if (amount > 0 && existing.length > 0) {
      const template = existing[0]!;
      for (let i = 0; i < Math.min(amount, TOKEN_CAP); i += 1) {
        const clone = createObject(game, template.card, controller, Zone.BATTLEFIELD, { kind: template.kind });
        clone.enteredBattlefieldTurn = game.turn;
        clone.summoningSick = true;
      }
    } else if (amount < 0) {
      for (const obj of existing.slice(0, -amount)) moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
    }
  }
}

/** One merged power/toughness effect per pump, rather than a thousand. */
function applyPumps(game: Game, pumps: readonly Pump[], cycles: number): void {
  for (const [template, count, power, toughness] of pumps) {
    const size = Math.max(Math.abs(power), Math.abs(toughness));
    if (!size) continue;
    const scale = Math.min(count * cycles, Math.max(1, Math.floor(PUMP_CAP / size)));
    const effect = template.effect as Effect;
    game.continuousEffects.push({
      ...template,
      effect: { ...effect, amount: { $: "Value", constant: power * scale }, amount2: { $: "Value", constant: toughness * scale } },
      timestamp: allocateTimestamp(game.ids),
    });
  }
}

// ---------------------------------------------------------------------------
// Ending and recording
// ---------------------------------------------------------------------------

/** CR 104.4b: a loop of mandatory actions that nothing can stop. */
function drawGame(game: Game, description: string): void {
  game.loopDraw = true;
  game.loops.push(`draw: ${description}`);
  logRecord(game, `Mandatory loop that nothing can stop (${description}); the game is a draw (CR 104.4b)`, { kind: "loop" });
  game.gameOver = true;
  game.winners = [];
}

function note(game: Game, outcome: string, description: string, cycles: number, reason: string): void {
  game.loops.push(`${outcome}: ${description}`);
  logRecord(game, `Loop ${outcome} (CR 732.2a): ${description} - ${cycles} more time${cycles === 1 ? "" : "s"}, ${reason}`, { kind: "loop" });
}

function nameOf(obj: GameObject | undefined | null): string {
  return (obj?.card as { name?: string } | null | undefined)?.name ?? "";
}

function labelOf(obj: GameObject | undefined | null): string {
  if (obj === null || obj === undefined) return "";
  const ability = obj.ability as { text?: string } | null;
  if (ability !== null && ability !== undefined) return ability.text || JSON.stringify(ability);
  return nameOf(obj);
}

function targetsOf(game: Game, targets: readonly (readonly number[])[] | readonly number[] | null | undefined): unknown[] {
  if (!targets || targets.length === 0) return [];
  return (targets as readonly unknown[]).map((item) => (Array.isArray(item) ? item.map((t) => targetOf(game, t as number)) : targetOf(game, item as number)));
}

function targetOf(game: Game, target: number): unknown[] {
  if (typeof target === "number") {
    if (isPlayerTarget(target)) return ["p", targetPlayer(target)];
    const obj = game.objects.get(target);
    if (obj !== undefined) return ["o", nameOf(obj) || labelOf(obj)];
  }
  return ["?"];
}
