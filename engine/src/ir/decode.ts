/**
 * Reading the parser's IR JSON (see docs/ir-json.md).
 *
 * - `complete` fills in the defaults the JSON leaves out, so the engine
 *   can read every field of an IR object.
 * - `enumValue` turns "Zone.GRAVEYARD" or "CardType.ARTIFACT|CREATURE"
 *   into the number the Python engine uses.
 * - `check` reports anything that is not IR version 1: an unknown class,
 *   field or enum member.
 *
 * `types.ts` and `defaults.ts` are generated from the Python IR classes by
 * `python -m mtgfish.tools.ir_export schema`. This file is hand-written.
 */
import { DEFAULTS, REQUIRED } from "./defaults";
import * as Types from "./types";
import type { CardDocument, FaceDocument } from "./types";

const ENUMS: Record<string, { values: Record<string, number>; flag: boolean }> = {};
for (const [key, table] of Object.entries(Types)) {
  if (key.endsWith("Values") && typeof table === "object") {
    ENUMS[key.slice(0, -"Values".length)] = { values: table as Record<string, number>, flag: false };
  } else if (key.endsWith("Bits") && typeof table === "object") {
    ENUMS[key.slice(0, -"Bits".length)] = { values: table as Record<string, number>, flag: true };
  }
}

/** Whether a string is an enum literal ("Zone.GRAVEYARD"), not text. */
export function isEnumLiteral(value: string): boolean {
  const dot = value.indexOf(".");
  return dot > 0 && value.slice(0, dot) in ENUMS;
}

/**
 * The number behind an enum literal ("Zone.GRAVEYARD", "CardType.A|B").
 * A number is returned as it is, so code written against this works with
 * IR version 2, which writes enums as numbers.
 */
export function enumValue(literal: string | number): number {
  if (typeof literal === "number") return literal;
  const dot = literal.indexOf(".");
  const name = literal.slice(0, dot);
  const entry = ENUMS[name];
  if (entry === undefined || dot < 0) throw new Error(`not an IR enum literal: ${literal}`);
  const members = entry.flag ? literal.slice(dot + 1).split("|") : [literal.slice(dot + 1)];
  let value = 0;
  for (const member of members) {
    const bit = entry.values[member];
    if (bit === undefined) throw new Error(`${name} has no member ${member}`);
    value |= bit;
  }
  return value;
}

/** The text of an IR string, unwrapping `{ $str }`. */
export function irString(value: Types.IRString): string {
  return typeof value === "string" ? value : value.$str;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The same IR with every default filled in, all the way down. The JSON
 * leaves out any field equal to its default; after this, every field the
 * class has is present.
 */
export function complete<T>(value: T): T {
  if (Array.isArray(value)) return value.map((item) => complete(item)) as T;
  if (!isObject(value)) return value;
  if ("$set" in value) return { $set: complete(value.$set) } as T;
  if ("$list" in value) return { $list: complete(value.$list) } as T;
  if ("$str" in value) return value;
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

/** Problems that make a value not IR version 1; empty when it is. */
export function check(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => check(item, `${path}/${i}`));
  if (typeof value === "string") {
    if (!isEnumLiteral(value)) return [];
    try {
      enumValue(value);
      return [];
    } catch (error) {
      return [`${path}: ${(error as Error).message}`];
    }
  }
  if (!isObject(value)) return [];
  if ("$set" in value) return check(value.$set, `${path}/$set`);
  if ("$list" in value) return check(value.$list, `${path}/$list`);
  if ("$str" in value) return typeof value.$str === "string" ? [] : [`${path}: bad $str`];
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
