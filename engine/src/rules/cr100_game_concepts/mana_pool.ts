/**
 * Mana kinds and the mana pool. Ported from ManaKind, ManaRestriction and
 * ManaPool in mtgfish/rules/cr100_game_concepts/cr106_mana.py; payment
 * solving (find_payment) comes with the core rules.
 *
 * Python keyed the pool by ManaKind, a frozen dataclass compared by value. A
 * JS Map compares objects by identity, so each kind has a canonical string
 * key and the pool maps key -> {kind, count}.
 */

import type { ObjectFilter } from "../../ir";
import { Color, colorLetters } from "../../kernel/enums";

/** "Spend this mana only on ..." (CR 106.6). `key` names the restriction. */
export interface ManaRestriction {
  readonly key: string;
  readonly filter: ObjectFilter | null;
}

/** The type of one unit of mana, and what constrains its use. */
export interface ManaKind {
  /** Color.NONE is colorless mana (CR 106.1b), not generic. */
  readonly color: Color;
  readonly snow: boolean;
  readonly restriction: ManaRestriction | null;
  /** CR 702.189a: kept through the combat phase's step ends. */
  readonly untilEndOfCombat: boolean;
}

export function manaKind(color: Color, options: Partial<Omit<ManaKind, "color">> = {}): ManaKind {
  return {
    color,
    snow: options.snow ?? false,
    restriction: options.restriction ?? null,
    untilEndOfCombat: options.untilEndOfCombat ?? false,
  };
}

/** Canonical identity of a kind: equal kinds have equal keys. */
export function manaKindKey(kind: ManaKind): string {
  return `${kind.color}|${kind.snow ? 1 : 0}|${kind.restriction?.key ?? ""}|${kind.untilEndOfCombat ? 1 : 0}`;
}

export function manaKindText(kind: ManaKind): string {
  let base = kind.color ? colorLetters(kind.color) : "C";
  if (kind.snow) base += "(snow)";
  if (kind.untilEndOfCombat) base += "(until end of combat)";
  if (kind.restriction !== null) base += `[${kind.restriction.key}]`;
  return base;
}

/** Stable, colour-canonical order, for deterministic payment and logging. */
function compareKinds(a: ManaKind, b: ManaKind): number {
  if (a.color !== b.color) return a.color - b.color;
  if (a.snow !== b.snow) return a.snow ? 1 : -1;
  const ra = a.restriction?.key ?? "";
  const rb = b.restriction?.key ?? "";
  if (ra !== rb) return ra < rb ? -1 : 1;
  if (a.untilEndOfCombat !== b.untilEndOfCombat) return a.untilEndOfCombat ? 1 : -1;
  return 0;
}

interface Bucket {
  kind: ManaKind;
  count: number;
}

/** Mana a player has available; empties at each step and phase end (CR 500.4). */
export interface ManaPool {
  buckets: Map<string, Bucket>;
}

export function newManaPool(): ManaPool {
  return { buckets: new Map() };
}

export function cloneManaPool(pool: ManaPool): ManaPool {
  const buckets = new Map<string, Bucket>();
  for (const [key, b] of pool.buckets) buckets.set(key, { kind: b.kind, count: b.count });
  return { buckets };
}

export function addMana(pool: ManaPool, kind: ManaKind, amount = 1): void {
  if (amount <= 0) return;
  const key = manaKindKey(kind);
  const bucket = pool.buckets.get(key);
  if (bucket) bucket.count += amount;
  else pool.buckets.set(key, { kind, count: amount });
}

export function removeMana(pool: ManaPool, kind: ManaKind, amount = 1): void {
  const key = manaKindKey(kind);
  const have = pool.buckets.get(key)?.count ?? 0;
  if (have < amount) {
    throw new RangeError(`cannot remove ${amount} ${manaKindText(kind)} from pool holding ${have}`);
  }
  if (have === amount) pool.buckets.delete(key);
  else (pool.buckets.get(key) as Bucket).count = have - amount;
}

export function manaOfKind(pool: ManaPool, kind: ManaKind): number {
  return pool.buckets.get(manaKindKey(kind))?.count ?? 0;
}

export function poolTotal(pool: ManaPool): number {
  let total = 0;
  for (const b of pool.buckets.values()) total += b.count;
  return total;
}

export function amountOfColor(pool: ManaPool, color: Color): number {
  let total = 0;
  for (const b of pool.buckets.values()) if (b.kind.color === color) total += b.count;
  return total;
}

/**
 * Empty the pool, returning how much was lost (CR 500.4). With
 * `keepCombatMana`, mana an effect keeps until end of combat stays.
 */
export function clearPool(pool: ManaPool, keepCombatMana = false): number {
  let lost = 0;
  for (const [key, b] of [...pool.buckets]) {
    if (keepCombatMana && b.kind.untilEndOfCombat) continue;
    lost += b.count;
    pool.buckets.delete(key);
  }
  return lost;
}

/** Deterministically ordered contents, for logging and hashing. */
export function poolSnapshot(pool: ManaPool): [ManaKind, number][] {
  return [...pool.buckets.values()]
    .sort((x, y) => compareKinds(x.kind, y.kind))
    .map((b) => [b.kind, b.count]);
}

export function poolText(pool: ManaPool): string {
  if (pool.buckets.size === 0) return "(empty)";
  return poolSnapshot(pool)
    .map(([k, n]) => `${n}x${manaKindText(k)}`)
    .join(" ");
}
