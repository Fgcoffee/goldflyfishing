/**
 * Reading IR objects at runtime. The IR leaves out fields at their default,
 * writes strings that look like enum members as {$str: ...}, writes sets as
 * {$set: [...]}, and (in version 1) writes enums as "Kind.MEMBER" strings.
 * These helpers read a field whichever way it is written, so the engine is
 * unaffected if enums become plain numbers (engine/status/rules.md).
 */

import { enumValue, irString, type IRString } from "../ir";

const DECODED = new Map<string, number>();

/** An enum or flag field as its number; `fallback` when absent. */
export function num(value: unknown, fallback = 0): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    let decoded = DECODED.get(value);
    if (decoded === undefined) {
      decoded = enumValue(value);
      DECODED.set(value, decoded);
    }
    return decoded;
  }
  throw new TypeError(`not an enum value: ${JSON.stringify(value)}`);
}

/** An enum field that may be null (e.g. `Effect.zone`). */
export function numOrNull(value: unknown): number | null {
  return value === undefined || value === null ? null : num(value);
}

/** A string field, unwrapping {$str}. */
export function str(value: IRString | undefined | null, fallback = ""): string {
  if (value === undefined || value === null) return fallback;
  return irString(value);
}

/** A list of strings (keywords, subtypes). */
export function strs(value: readonly IRString[] | undefined | null): string[] {
  return (value ?? []).map((v) => irString(v));
}

/** A {$set: [...]} field as a Set of numbers; `fallback` when absent. */
export function numSet(value: { $set: readonly unknown[] } | undefined | null, fallback: readonly number[] = []): Set<number> {
  if (value === undefined || value === null) return new Set(fallback);
  return new Set(value.$set.map((v) => num(v)));
}

/** A list field, empty when absent. */
export function list<T>(value: readonly T[] | undefined | null): readonly T[] {
  return value ?? [];
}

/** A boolean field. */
export function flag(value: boolean | undefined | null): boolean {
  return value === true;
}

/**
 * Structural equality of IR values: what Python's dataclass `==` gave for
 * abilities and effects (an ability "still in" a characteristic set, a grant
 * already recorded). Key order is ignored.
 */
export function irEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (!irEqual(a[i], b[i])) return false;
    return true;
  }
  if (Array.isArray(b)) return false;
  const ka = Object.keys(a as object).filter((k) => (a as Record<string, unknown>)[k] !== undefined);
  const kb = Object.keys(b as object).filter((k) => (b as Record<string, unknown>)[k] !== undefined);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!irEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])) return false;
  }
  return true;
}

/** Whether `items` holds a structurally equal value. */
export function irIncludes(items: readonly unknown[], item: unknown): boolean {
  return items.some((x) => x === item || irEqual(x, item));
}
