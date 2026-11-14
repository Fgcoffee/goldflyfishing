/**
 * Evaluating dynamic values ("equal to the number of Swamps you control").
 * Ported from mtgfish/rules/kernel/values.py.
 */

import type { ObjectFilter, Value } from "../../ir";
import { CardType, Zone } from "../../kernel/enums";
import { player as playerOf, turnHistoryCountKey, turnHistoryKey, type Game } from "../../kernel/game";
import { counterCount, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { charsManaValue, hasSubtype, hasType, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { list, num, numSet } from "../ir_read";
import { fromIR } from "../mana/cost";
import { ValueKind, PlayerScope } from "./enums";
import { combatOf, find, matches, resolvePlayers } from "./matching";

export interface EvaluateOptions {
  readonly source?: ObjectId;
  readonly controller?: PlayerId;
  readonly xValue?: number;
  readonly eventAmount?: number;
  readonly dieResults?: readonly number[];
  /** What the resolution last acted on ("each creature destroyed this way"). */
  readonly remembered?: readonly ObjectId[];
  /** How much the resolution has done so far, by event kind ("the life lost this way"). */
  readonly thisWay?: ReadonlyMap<number, number>;
}

/**
 * CR 107.2, 903.4f: a number that could not be determined is worth zero, but
 * a cost must tell it apart (Python's Undeterminable, an int subclass). Here
 * the evaluation that produced it raises this flag; `wasUndeterminable()`
 * reads it right after a top-level `evaluate`.
 */
let lastUndeterminable = false;
export function wasUndeterminable(): boolean {
  return lastUndeterminable;
}

const YOU = { $: "PlayerFilter", scope: PlayerScope.YOU } as const;

function popcount(value: number): number {
  let count = 0;
  for (let v = value >>> 0; v; v &= v - 1) count++;
  return count;
}

/** What a Value currently is. */
export function evaluate(game: Game, value: Value | number, options: EvaluateOptions = {}): number {
  lastUndeterminable = false;
  return evaluateInner(game, value, options);
}

function evaluateInner(game: Game, value: Value | number, options: EvaluateOptions): number {
  // CR 107.1: a number written as a plain int is that number.
  if (typeof value === "number") return value;
  const source = options.source ?? NO_OBJECT;
  const controller = options.controller ?? NO_PLAYER;
  const remembered = options.remembered ?? [];
  const kind = num(value.kind);

  if (kind === ValueKind.CONSTANT) return value.constant ?? 0;
  if (kind === ValueKind.EVENT_AMOUNT) return options.eventAmount ?? 0;
  // CR 706.4: several dice total.
  if (kind === ValueKind.DIE_ROLL_RESULT) return (options.dieResults ?? []).reduce((a, b) => a + b, 0);
  if (kind === ValueKind.EVENT_COUNT_THIS_TURN) return thisTurn(game, value, controller, false);
  if (kind === ValueKind.EVENT_AMOUNT_THIS_TURN) return thisTurn(game, value, controller, true);
  // CR 608.2c: what this resolution has done so far, by amount.
  if (kind === ValueKind.AMOUNT_THIS_WAY) {
    const tally = options.thisWay;
    return list(value.event_kinds).reduce<number>((t, e) => t + (tally?.get(num(e)) ?? 0), 0);
  }
  // CR 702.23a-b: the creatures blocking the source now.
  if (kind === ValueKind.BLOCKERS_OF_SOURCE) {
    const combat = combatOf(game);
    return combat === null ? 0 : (combat.blockers?.get(source) ?? []).length;
  }
  // CR 702.125b: players who have left the game are not counted.
  if (kind === ValueKind.PLAYER_COUNT) {
    if (!value.players) return 0;
    return resolvePlayers(game, value.players, { controller }).filter((p) => !playerOf(game, p).hasLost).length;
  }
  if (kind === ValueKind.X) {
    const obj = game.objects.get(source);
    return obj !== undefined ? obj.xValue : (options.xValue ?? 0);
  }
  if (kind === ValueKind.COUNT) {
    if (!value.filter) return 0;
    return among(game, value.filter, source, controller, remembered).length;
  }
  if (value.filter && NAMED_OBJECT_VALUES.has(kind)) return ofNamedObjects(game, value, source, controller, remembered);

  if (kind === ValueKind.POWER || kind === ValueKind.TOUGHNESS || kind === ValueKind.MANA_VALUE) {
    // In a resolving instruction "its" is what the previous instruction acted on (CR 608.2h).
    const subject = value.of_affected && remembered.length > 0 ? (remembered[0] as ObjectId) : source;
    const obj = game.objects.get(subject);
    if (obj === undefined) return 0;
    return characteristicOf(characteristicsOf(game, obj), kind);
  }
  if (kind === ValueKind.COUNTERS) {
    const obj = game.objects.get(source);
    return obj !== undefined ? counterCount(obj, value.counter_type ?? "") : 0;
  }
  if (kind === ValueKind.MANA_SPENT) {
    const obj = game.objects.get(source);
    return obj?.manaSpent ?? 0;
  }
  if (kind === ValueKind.COLOURS_AMONG) {
    if (!value.filter) return 0;
    let colours = 0;
    for (const obj of among(game, value.filter, source, controller, remembered)) {
      colours |= characteristicsOf(game, obj).colors;
    }
    return popcount(colours);
  }
  // CR 903.4: fixed before the game; CR 903.4f: undefined with no commander.
  if (kind === ValueKind.COMMANDER_COLOUR_IDENTITY) {
    const identity = controller !== NO_PLAYER ? commanderColorIdentity(game, controller) : null;
    if (identity === null) {
      lastUndeterminable = true;
      return 0;
    }
    return popcount(identity);
  }
  if (kind === ValueKind.COLORS_OF_MANA_SPENT) {
    const obj = game.objects.get(source);
    return obj !== undefined ? popcount(obj.manaSpentColors) : 0;
  }
  if (kind === ValueKind.PARTY_SIZE) return controller !== NO_PLAYER ? partySize(game, controller) : 0;
  if (kind === ValueKind.DEVOTION) return devotion(game, value, controller, source);
  if (kind === ValueKind.COST_PAID_POWER) {
    const obj = game.objects.get(source);
    if (obj === undefined) return 0;
    let total = 0;
    for (const id of obj.costPaidObjects) total += paidAsItWas(game, id)?.power ?? 0;
    return total;
  }
  if (kind === ValueKind.COST_PAID) {
    // "The sacrificed creature's toughness": each object the cost used, as it last existed (CR 608.2h).
    const obj = game.objects.get(source);
    const first = list(value.operands)[0];
    if (obj === undefined || first === undefined) return 0;
    const wanted = num(first.kind);
    let total = 0;
    for (const id of obj.costPaidObjects) {
      const chars = paidAsItWas(game, id);
      if (chars === null) continue;
      if (wanted === ValueKind.TOUGHNESS) total += chars.toughness ?? 0;
      else if (wanted === ValueKind.MANA_VALUE) total += charsManaValue(chars);
      else if (wanted === ValueKind.POWER) total += chars.power ?? 0;
    }
    return total;
  }
  if (kind === ValueKind.GREATEST_AMONG || kind === ValueKind.LEAST_AMONG || kind === ValueKind.TOTAL_AMONG) {
    return superlative(game, value, kind, source, controller, options.xValue ?? 0, remembered);
  }
  if (
    kind === ValueKind.LIFE_TOTAL ||
    kind === ValueKind.CARDS_IN_HAND ||
    kind === ValueKind.POISON_COUNTERS ||
    kind === ValueKind.ENERGY
  ) {
    return playerValue(game, value, kind, controller);
  }
  return arithmetic(game, value, kind, options);
}

function characteristicOf(chars: Characteristics, kind: number): number {
  if (kind === ValueKind.POWER) return chars.power ?? 0;
  if (kind === ValueKind.TOUGHNESS) return chars.toughness ?? 0;
  return charsManaValue(chars);
}

const NAMED_OBJECT_VALUES = new Set<number>([
  ValueKind.POWER,
  ValueKind.TOUGHNESS,
  ValueKind.MANA_VALUE,
  ValueKind.COUNTERS,
  ValueKind.MANA_SPENT,
]);

/** "Its power", "that card's mana value": a characteristic of the object the text names; several total. */
function ofNamedObjects(
  game: Game,
  value: Value,
  source: ObjectId,
  controller: PlayerId,
  remembered: readonly ObjectId[],
): number {
  const kind = num(value.kind);
  let total = 0;
  for (const obj of among(game, value.filter as ObjectFilter, source, controller, remembered)) {
    if (kind === ValueKind.COUNTERS) total += counterCount(obj, value.counter_type ?? "");
    else if (kind === ValueKind.MANA_SPENT) total += obj.manaSpent;
    else total += characteristicOf(characteristicsOf(game, obj), kind);
  }
  return total;
}

/**
 * An object a cost used, as it is or as it last existed (CR 608.2h). One
 * still where it was (a creature tapped to pay) is read as it is now; one
 * the payment moved is read from the record taken as it was paid
 * (`game.costPaidLastKnown`, written by paying.ts), since the object left
 * behind is off the board and would read as printed.
 */
function paidAsItWas(game: Game, objectId: ObjectId): Characteristics | null {
  const paid = game.objects.get(objectId);
  if (paid === undefined) return null;
  if (paid.supersededBy) {
    const known = game.costPaidLastKnown.get(objectId) as Characteristics | undefined;
    if (known !== undefined) return known;
  }
  return characteristicsOf(game, paid);
}

/**
 * The objects a value's filter ranges over. A remembered filter ("each
 * creature destroyed this way") names only what the resolution acted on,
 * asked as it was then; a filter naming ids is asked of those objects.
 */
export function among(
  game: Game,
  spec: ObjectFilter,
  source: ObjectId,
  controller: PlayerId,
  remembered: readonly ObjectId[],
): GameObject[] {
  const specific = list(spec.specific);
  const anyZone = numSet(spec.zones, [Zone.BATTLEFIELD]).size === 0;
  if (specific.length > 0 && anyZone && !spec.remembered) {
    const described: ObjectFilter = { ...spec, specific: [], zones: { $set: [] }, count: null, up_to: false };
    const out: GameObject[] = [];
    for (const id of specific) {
      const obj = game.objects.get(id);
      if (obj !== undefined && matches(game, obj, described, { source, controller, allowStale: true })) out.push(obj);
    }
    return out;
  }
  if (!spec.remembered) return find(game, spec, { source, controller });
  const described: ObjectFilter = { ...spec, remembered: false, zones: { $set: [] }, count: null, up_to: false };
  const found: GameObject[] = [];
  for (const id of remembered) {
    const obj = game.objects.get(id);
    if (obj === undefined || found.includes(obj)) continue;
    if (matches(game, obj, described, { source, controller, allowStale: true })) found.push(obj);
  }
  return found;
}

/** How many times (or, with `amounts`, how much) an event happened this turn, for the named players. */
function thisTurn(game: Game, value: Value, controller: PlayerId, amounts: boolean): number {
  const players = resolvePlayers(game, value.players ?? YOU, { controller });
  let total = 0;
  for (const eventKind of list(value.event_kinds)) {
    for (const p of players) {
      const key = amounts ? turnHistoryKey(num(eventKind), p) : turnHistoryCountKey(num(eventKind), p);
      total += game.turnHistory.get(key) ?? 0;
    }
  }
  return total;
}

/** CR 700.5: coloured mana symbols among permanents a player controls, per symbol; a hybrid counts once. */
function devotion(game: Game, value: Value, controller: PlayerId, source: ObjectId): number {
  let wanted = num(value.colors);
  if (value.filter?.of_chosen_color) {
    const chooser = game.objects.get(source);
    wanted = chooser?.chosenColor ?? 0;
  }
  if (!wanted) return 0;
  const owners = new Set(resolvePlayers(game, value.players ?? YOU, { controller }));
  if (owners.size === 0) owners.add(controller);
  let total = 0;
  for (const id of game.battlefield) {
    const obj = game.objects.get(id);
    if (obj === undefined || !owners.has(obj.controller)) continue;
    for (const symbol of fromIR(characteristicsOf(game, obj).manaCost).symbols) {
      if (symbol.colors & wanted) total += 1;
    }
  }
  return total;
}

/** "The greatest mana value among permanents you control": the inner value per object, as its source. */
function superlative(
  game: Game,
  value: Value,
  kind: number,
  source: ObjectId,
  controller: PlayerId,
  xValue: number,
  remembered: readonly ObjectId[],
): number {
  const inner = list(value.operands)[0];
  if (!value.filter || inner === undefined) return 0;
  const found = among(game, value.filter, source, controller, remembered);
  if (found.length === 0) return 0;
  const amounts = found.map((obj) => evaluateInner(game, inner, { source: obj.id, controller, xValue }));
  if (kind === ValueKind.TOTAL_AMONG) return amounts.reduce((a, b) => a + b, 0);
  return kind === ValueKind.GREATEST_AMONG ? Math.max(...amounts) : Math.min(...amounts);
}

/** A player-scoped value; several players give the largest. */
function playerValue(game: Game, value: Value, kind: number, controller: PlayerId): number {
  const players = resolvePlayers(game, value.players ?? YOU, { controller });
  if (players.length === 0) return 0;
  const amounts = players.map((id) => {
    const p = playerOf(game, id);
    if (kind === ValueKind.LIFE_TOTAL) return p.life;
    if (kind === ValueKind.CARDS_IN_HAND) return p.hand.length;
    if (kind === ValueKind.POISON_COUNTERS) return p.poison;
    return p.energy;
  });
  return Math.max(...amounts);
}

/** Python floor division. */
function floorDiv(a: number, b: number): number {
  return Math.floor(a / b);
}

function arithmetic(game: Game, value: Value, kind: number, options: EvaluateOptions): number {
  const operands = list(value.operands).map((operand) => evaluateInner(game, operand, options));
  const constant = value.constant ?? 0;
  if (kind === ValueKind.SUM) return operands.reduce((a, b) => a + b, 0) + constant;
  if (kind === ValueKind.PRODUCT) return operands.reduce((a, b) => a * b, constant || 1);
  if (kind === ValueKind.DIFFERENCE) {
    if (operands.length === 0) return 0;
    return operands.slice(1).reduce((a, b) => a - b, operands[0] as number);
  }
  if (kind === ValueKind.HALF_ROUNDED_UP) return operands.length > 0 ? floorDiv((operands[0] as number) + 1, 2) : 0;
  if (kind === ValueKind.HALF_ROUNDED_DOWN) return operands.length > 0 ? floorDiv(operands[0] as number, 2) : 0;
  if (kind === ValueKind.MAXIMUM) return operands.length > 0 ? Math.max(...operands) : 0;
  if (kind === ValueKind.MINIMUM) return operands.length > 0 ? Math.min(...operands) : 0;
  return 0;
}

/** CR 903.4: the combined colour identity of a player's commanders; null with none (CR 903.4f). */
export function commanderColorIdentity(game: Game, playerId: PlayerId): number | null {
  const commanders = playerOf(game, playerId).commanders;
  if (commanders.length === 0) return null;
  let identity = 0;
  for (const id of commanders) {
    const card = game.objects.get(id)?.card as { color_identity?: unknown } | null | undefined;
    if (card) identity |= num(card.color_identity);
  }
  return identity;
}

const PARTY_ROLES = ["Cleric", "Rogue", "Warrior", "Wizard"] as const;

/** CR 700.8: up to one creature in each role, each creature in one role - the largest matching. */
export function partySize(game: Game, playerId: PlayerId): number {
  const creatures: Set<string>[] = [];
  for (const id of game.battlefield) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.controller !== playerId) continue;
    const chars = characteristicsOf(game, obj);
    if (!hasType(chars, CardType.CREATURE)) continue;
    const roles = new Set(PARTY_ROLES.filter((r) => hasSubtype(chars, r)));
    if (roles.size > 0) creatures.push(roles);
  }
  // Four roles: try assigning roles in order, each to an unused creature (exact and cheap).
  let best = 0;
  const used = new Set<number>();
  const assign = (roleIndex: number, filled: number): void => {
    if (roleIndex === PARTY_ROLES.length) {
      best = Math.max(best, filled);
      return;
    }
    const role = PARTY_ROLES[roleIndex] as string;
    assign(roleIndex + 1, filled);
    creatures.forEach((roles, i) => {
      if (used.has(i) || !roles.has(role)) return;
      used.add(i);
      assign(roleIndex + 1, filled + 1);
      used.delete(i);
    });
  };
  assign(0, 0);
  return best;
}
