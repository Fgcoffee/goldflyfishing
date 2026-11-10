/**
 * Mana kinds and the mana pool (CR 106, 500.4). Ported from ManaKind,
 * SpendOnlyOn and ManaPool in mtgfish/rules/cr100_game_concepts/cr106_mana.py
 * (first ported by the rules session; payment is in ./payment.ts).
 *
 * Python keyed the pool by ManaKind, a frozen dataclass compared by value. A
 * JS Map compares objects by identity, so each kind has a canonical string
 * key and the pool maps key -> {kind, count}.
 */

import type { ObjectFilter } from "../../ir";
import { enumValue } from "../../ir/decode";
import { Color, colorLetters } from "../../kernel/enums";

/** "Spend this mana only on ..." (CR 106.6). `key` names the restriction. */
export interface ManaRestriction {
  readonly key: string;
  readonly filter: ObjectFilter | null;
}

/**
 * What a payment is for, as far as a restriction can tell: the printed card
 * types and subtypes of the spell being cast. `null` for a bare payability
 * check (nothing is being paid for yet), `undefined` fields for something
 * that isn't a spell (a token, an emblem, an ability).
 */
export interface PaymentContext {
  readonly types?: number;
  readonly subtypes?: readonly string[];
}

/** An IR string field's text, whether plain or wrapped as `{ $str }` (IR v1). */
function irText(value: unknown): string {
  return typeof value === "string" ? value : (value as { $str: string }).$str;
}

function filterTypes(value: unknown): number {
  return value === undefined || value === null ? 0 : enumValue(value as number | string);
}

/**
 * Whether a restriction lets its mana pay for `context` (SpendOnlyOn.permits).
 * A restriction with no filter permits nothing: an unreadable rider must not
 * widen the mana. With no context (a bare payability check) it says yes, so
 * the mana still counts as available. Tested against printed types, as
 * Python does.
 */
export function restrictionPermits(restriction: ManaRestriction, context: PaymentContext | null): boolean {
  const filter = restriction.filter;
  if (filter === null) return false;
  if (context === null) return true;
  if (context.types === undefined) return true; // not a spell being cast
  const wanted = filterTypes(filter.types_all) | filterTypes(filter.types_any);
  if (wanted && (context.types & wanted) === 0) return false;
  const subtypes = [...(filter.subtypes_all ?? []), ...(filter.subtypes_any ?? [])].map(irText);
  if (subtypes.length > 0) {
    const have = new Set(context.subtypes ?? []);
    if (!subtypes.some((s) => have.has(s))) return false;
  }
  return true;
}

/** Whether a unit of this kind may be spent on `context` (CR 106.6). */
export function kindUsableFor(kind: ManaKind, context: PaymentContext | null): boolean {
  return kind.restriction === null || restrictionPermits(kind.restriction, context);
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
export function compareKinds(a: ManaKind, b: ManaKind): number {
  if (a.color !== b.color) return a.color - b.color;
  if (a.snow !== b.snow) return a.snow ? 1 : -1;
  const ra = a.restriction?.key ?? "";
  const rb = b.restriction?.key ?? "";
  if (ra !== rb) return ra < rb ? -1 : 1;
  if (a.untilEndOfCombat !== b.untilEndOfCombat) return a.untilEndOfCombat ? 1 : -1;
  return 0;
}

export interface Bucket {
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

/** How much of the pool could be spent on `context` (restricted mana excluded). */
export function usableFor(pool: ManaPool, context: PaymentContext | null): number {
  let total = 0;
  for (const b of pool.buckets.values()) if (kindUsableFor(b.kind, context)) total += b.count;
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
