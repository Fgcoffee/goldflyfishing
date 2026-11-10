/**
 * Reading the parser's IR JSON, version 2 (see docs/ir-json.md).
 *
 * - Enums are already numbers, the same numbers as engine/src/kernel/enums.ts.
 * - `complete` fills in the defaults the JSON leaves out and unwraps
 *   `{ $int: n }` (a plain integer in an enum-typed field), so the engine can
 *   read every field of an IR object directly.
 * - `check` reports anything that is not this IR version: an unknown class
 *   or field, a missing required field.
 * - `memberName("Zone", 3)` gives "GRAVEYARD", for messages and logs.
 *
 * `types.ts` and `defaults.ts` are generated from the Python IR classes by
 * `python -m mtgfish.tools.ir_export schema`. This file is hand-written.
 */
import { DEFAULTS, REQUIRED } from "./defaults";
import * as Types from "./types";
import type { CardDocument, FaceDocument } from "./types";

const TABLES: Record<string, { values: Record<string, number>; flag: boolean }> = {};
for (const [key, table] of Object.entries(Types)) {
  if (key.endsWith("Values") && typeof table === "object") {
    TABLES[key.slice(0, -"Values".length)] = { values: table as Record<string, number>, flag: false };
  } else if (key.endsWith("Bits") && typeof table === "object") {
    TABLES[key.slice(0, -"Bits".length)] = { values: table as Record<string, number>, flag: true };
  }
}

/** The member name(s) of an enum value: "GRAVEYARD", or "ARTIFACT|CREATURE" for a flag. */
export function memberName(enumName: string, value: number): string {
  const table = TABLES[enumName];
  if (table === undefined) throw new Error(`not an IR enum: ${enumName}`);
  const entries = Object.entries(table.values);
  if (!table.flag || value === 0) {
    const found = entries.find(([, v]) => v === value);
    return found === undefined ? String(value) : found[0];
  }
  const names = entries.filter(([, bit]) => bit !== 0 && (value & bit) === bit).map(([name]) => name);
  return names.length > 0 ? names.join("|") : String(value);
}

/**
 * An enum's number. Version 2 already gives numbers, so a number is returned
 * as it is; a version 1 literal ("Zone.GRAVEYARD", "CardType.A|B") is still
 * understood, for code written before the switch.
 */
export function enumValue(value: number | string): number {
  if (typeof value === "number") return value;
  const dot = value.indexOf(".");
  const table = dot > 0 ? TABLES[value.slice(0, dot)] : undefined;
  if (table === undefined) throw new Error(`not an IR enum literal: ${value}`);
  let result = 0;
  for (const member of value.slice(dot + 1).split("|")) {
    const bit = table.values[member];
    if (bit === undefined) throw new Error(`${value.slice(0, dot)} has no member ${member}`);
    result |= bit;
  }
  return result;
}

/** A string field's text. Version 2 strings are plain; kept for older callers. */
export function irString(value: string): string {
  return value;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The same IR with every default filled in, all the way down, and every
 * `{ $int: n }` unwrapped to `n`. The JSON leaves out any field equal to its
 * default; after this, every field the class has is present.
 */
export function complete<T>(value: T): T {
  if (Array.isArray(value)) return value.map((item) => complete(item)) as T;
  if (!isObject(value)) return value;
  if ("$int" in value) return value.$int as T;
  if ("$set" in value) return { $set: complete(value.$set) } as T;
  if ("$list" in value) return { $list: complete(value.$list) } as T;
  const name = value.$;
  if (typeof name !== "string") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = complete(item);
    return out as T;
  }
  const defaults = DEFAULTS[name];
  if (defaults === undefined) throw new Error(`unknown IR class ${name}`);
  const out: Record<string, unknown> = { $: name };
  for (const [key, item] of Object.entries(defaults)) out[key] = complete(clone(item));
  for (const [key, item] of Object.entries(value)) {
    if (key !== "$") out[key] = complete(item);
  }
  return out as T;
}

/** Problems that make a value not this IR version; empty when it is. */
export function check(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => check(item, `${path}/${i}`));
  if (!isObject(value)) return [];
  if ("$int" in value) return Number.isInteger(value.$int) ? [] : [`${path}: bad $int`];
  if ("$set" in value) return check(value.$set, `${path}/$set`);
  if ("$list" in value) return check(value.$list, `${path}/$list`);
  const name = value.$;
  if (typeof name !== "string") {
    return Object.entries(value).flatMap(([key, item]) => check(item, `${path}/${key}`));
  }
  const defaults = DEFAULTS[name];
  const required = REQUIRED[name];
  if (defaults === undefined || required === undefined) return [`${path}: unknown IR class ${name}`];
  const problems: string[] = [];
  for (const field of required) {
    if (!(field in value)) problems.push(`${path}: ${name} is missing ${field}`);
  }
  for (const [key, item] of Object.entries(value)) {
    if (key === "$") continue;
    if (!(key in defaults) && !required.includes(key)) {
      problems.push(`${path}: ${name} has no field ${key}`);
      continue;
    }
    problems.push(...check(item, `${path}/${key}`));
  }
  return problems;
}

/** A card document, checked: throws if it is not this IR version. */
export function readCard(document: unknown): CardDocument {
  if (!isObject(document) || document.ir_version !== Types.IR_VERSION) {
    throw new Error(`not an IR version ${Types.IR_VERSION} card document`);
  }
  const problems = check(document.faces);
  if (problems.length > 0) throw new Error(`bad IR: ${problems.slice(0, 5).join("; ")}`);
  return document as unknown as CardDocument;
}

/** A face's abilities with every default filled in. */
export function faceAbilities(face: FaceDocument): Types.Ability[] {
  return complete(face.abilities);
}
