/**
 * Mana symbols and mana costs (CR 107.4, 202.2, 202.3, 601.2f, 118.7).
 * Ported from ManaSymbol, parse_mana_symbol and ManaCost in
 * mtgfish/rules/cr100_game_concepts/cr106_mana.py.
 *
 * A symbol here is plain data with numeric fields, the same shape as the IR's
 * ManaSymbol: `kind` a ManaSymbolKind number, `colors` a Color flag. `fromIR`
 * reads an IR cost (string or numeric enums) into this shape. Costs are
 * immutable; arithmetic returns a new cost. Python compared ManaCost by
 * value; use `costKey` wherever a cost is a map or set key.
 */

import { ManaSymbolKindValues } from "../../ir";
import { enumValue } from "../../ir/decode";
import { Color, LETTER_TO_COLOR } from "../../kernel/enums";

/** CR 107.4: the kinds of mana symbol, grouped by how each is paid. */
export const ManaSymbolKind = {
  GENERIC: ManaSymbolKindValues.GENERIC as number, // {1}, {2}: any type of mana
  VARIABLE: ManaSymbolKindValues.VARIABLE as number, // {X}: generic once chosen
  COLORED: ManaSymbolKindValues.COLORED as number, // {W}: only white
  COLORLESS: ManaSymbolKindValues.COLORLESS as number, // {C}: colorless mana, not generic
  SNOW: ManaSymbolKindValues.SNOW as number, // {S}: mana from a snow source (CR 107.4h)
  HYBRID: ManaSymbolKindValues.HYBRID as number, // {W/U}
  MONOCOLOR_HYBRID: ManaSymbolKindValues.MONOCOLOR_HYBRID as number, // {2/W}: two generic or one white
  PHYREXIAN: ManaSymbolKindValues.PHYREXIAN as number, // {W/P}: a color, or 2 life
  HALF: ManaSymbolKindValues.HALF as number, // un-set only
  INFINITY: ManaSymbolKindValues.INFINITY as number, // un-set only
} as const;

export interface ManaSymbol {
  readonly kind: number;
  /** Every color that can pay it: one bit for {W}, two for {W/U}. */
  readonly colors: Color;
  readonly generic: number;
  readonly text: string;
}

export interface ManaCost {
  readonly symbols: readonly ManaSymbol[];
}

/** Raised for a `{...}` that is not a mana symbol - fatal, never skipped. */
export class UnknownManaSymbol extends Error {
  override name = "UnknownManaSymbol";
}

export function symbol(kind: number, options: Partial<Omit<ManaSymbol, "kind">> = {}): ManaSymbol {
  return { kind, colors: options.colors ?? Color.NONE, generic: options.generic ?? 0, text: options.text ?? "" };
}

// -- payment predicates -------------------------------------------------------

/** Symbols in the undifferentiated generic lump: {1} and {X}. */
export function isGeneric(s: ManaSymbol): boolean {
  return s.kind === ManaSymbolKind.GENERIC || s.kind === ManaSymbolKind.VARIABLE;
}

/** Life payable in place of mana (CR 107.4f); 0 unless Phyrexian. */
export function lifeCost(s: ManaSymbol): number {
  return s.kind === ManaSymbolKind.PHYREXIAN ? 2 : 0;
}

/** True when the payer picks between materially different payments. */
export function isChoice(s: ManaSymbol): boolean {
  return s.kind === ManaSymbolKind.MONOCOLOR_HYBRID || s.kind === ManaSymbolKind.PHYREXIAN;
}

/** The part of a mana kind a symbol cares about. */
export interface ManaTypeLike {
  readonly color: Color;
  readonly snow: boolean;
}

/** Whether one unit of mana of this kind can pay the symbol (CR 107.4a-c, e, h). */
export function accepts(s: ManaSymbol, kind: ManaTypeLike): boolean {
  const k = s.kind;
  if (k === ManaSymbolKind.COLORED || k === ManaSymbolKind.HYBRID) {
    if (s.colors === Color.NONE) return kind.color === Color.NONE;
    return kind.color !== Color.NONE && (kind.color & s.colors) !== 0;
  }
  if (k === ManaSymbolKind.PHYREXIAN || k === ManaSymbolKind.MONOCOLOR_HYBRID) {
    return kind.color !== Color.NONE && (kind.color & s.colors) !== 0;
  }
  if (k === ManaSymbolKind.COLORLESS) return kind.color === Color.NONE;
  // {S} constrains the source, not the type (CR 107.4h).
  if (k === ManaSymbolKind.SNOW) return kind.snow;
  return isGeneric(s);
}

// -- derived characteristics --------------------------------------------------

/** One symbol's contribution to mana value (CR 202.3). {X} is 0 off the stack. */
export function symbolManaValue(s: ManaSymbol): number {
  const k = s.kind;
  if (k === ManaSymbolKind.GENERIC || k === ManaSymbolKind.MONOCOLOR_HYBRID) return s.generic;
  if (k === ManaSymbolKind.VARIABLE) return 0;
  if (k === ManaSymbolKind.INFINITY) return 1_000_000;
  // Half (un-set only) rounds up so mana value stays an integer (CR 107.1).
  return 1;
}

/** Colors a symbol gives its object (CR 202.2); {C} and {S} give none. */
export function colorContribution(s: ManaSymbol): Color {
  const k = s.kind;
  if (
    k === ManaSymbolKind.COLORED ||
    k === ManaSymbolKind.HYBRID ||
    k === ManaSymbolKind.MONOCOLOR_HYBRID ||
    k === ManaSymbolKind.PHYREXIAN
  ) {
    return s.colors;
  }
  return Color.NONE;
}

// -- parsing ------------------------------------------------------------------

function isDigits(text: string): boolean {
  return /^[0-9]+$/.test(text);
}

/** Parse the inside of one `{...}` mana symbol; throws UnknownManaSymbol otherwise. */
export function parseManaSymbol(body: string): ManaSymbol {
  const raw = body.trim();
  const text = `{${raw}}`;
  const upper = raw.toUpperCase();

  if (isDigits(upper)) return symbol(ManaSymbolKind.GENERIC, { generic: Number.parseInt(upper, 10), text });
  if (upper === "X" || upper === "Y" || upper === "Z") return symbol(ManaSymbolKind.VARIABLE, { text });
  const single = LETTER_TO_COLOR.get(upper);
  if (single !== undefined) return symbol(ManaSymbolKind.COLORED, { colors: single, text });
  if (upper === "C") return symbol(ManaSymbolKind.COLORLESS, { text });
  if (upper === "S") return symbol(ManaSymbolKind.SNOW, { text });
  if (raw === "∞" || raw === "INF") return symbol(ManaSymbolKind.INFINITY, { text });
  if (raw === "½" || (upper.startsWith("H") && upper.length === 2)) {
    const colors = upper.length === 2 ? (LETTER_TO_COLOR.get(upper.slice(1)) ?? Color.NONE) : Color.NONE;
    return symbol(ManaSymbolKind.HALF, { colors, text });
  }

  if (upper.includes("/")) {
    let parts = upper.split("/");
    const phyrexian = parts[parts.length - 1] === "P";
    if (phyrexian) parts = parts.slice(0, -1);

    let colors: Color = Color.NONE;
    let generic = 0;
    let sawColorless = false;
    for (const part of parts) {
      const color = LETTER_TO_COLOR.get(part);
      if (isDigits(part)) generic = Number.parseInt(part, 10);
      else if (part === "C") sawColorless = true;
      else if (color !== undefined) colors |= color;
      else throw new UnknownManaSymbol(`unrecognised component '${part}' in ${text}`);
    }

    if (phyrexian) return symbol(ManaSymbolKind.PHYREXIAN, { colors, text });
    if (generic) return symbol(ManaSymbolKind.MONOCOLOR_HYBRID, { colors, generic, text });
    if (sawColorless && colors === Color.NONE) return symbol(ManaSymbolKind.COLORLESS, { text });
    return symbol(ManaSymbolKind.HYBRID, { colors, text });
  }

  throw new UnknownManaSymbol(`not a mana symbol: ${text}`);
}

/** CR 107.4d: {0}, payable with no resources - distinct from no mana cost at all. */
export const ZERO_COST: ManaCost = Object.freeze({ symbols: Object.freeze([]) as readonly ManaSymbol[] });

/** Parse a Scryfall mana cost such as "{2}{W}{W}". Empty or absent is the empty cost. */
export function parseCost(text: string | null | undefined): ManaCost {
  if (!text) return { symbols: [] };
  const symbols: ManaSymbol[] = [];
  for (const match of text.matchAll(/\{([^}]*)\}/g)) symbols.push(parseManaSymbol(match[1] ?? ""));
  return { symbols };
}

/** `amount` generic mana; zero or less is the empty cost (CR 107.1b, 107.4d). */
export function genericCost(amount: number): ManaCost {
  if (amount <= 0) return { symbols: [] };
  return { symbols: [symbol(ManaSymbolKind.GENERIC, { generic: amount, text: `{${amount}}` })] };
}

/** An IR ManaCost (version 1 or 2) as a cost here. */
export function fromIR(cost: { readonly symbols?: readonly unknown[] } | null | undefined): ManaCost {
  const symbols = (cost?.symbols ?? []).map((raw) => {
    const s = raw as { kind?: number | string; colors?: number | string; generic?: number; text?: string };
    return symbol(enumValue(s.kind ?? ManaSymbolKind.GENERIC), {
      colors: s.colors === undefined ? Color.NONE : enumValue(s.colors),
      generic: s.generic ?? 0,
      text: s.text ?? "",
    });
  });
  return { symbols };
}

// -- derived characteristics of a cost ----------------------------------------

/** CR 202.3; {X} contributes 0. */
export function manaValue(cost: ManaCost): number {
  return cost.symbols.reduce((total, s) => total + symbolManaValue(s), 0);
}

export function variableCount(cost: ManaCost): number {
  return cost.symbols.filter((s) => s.kind === ManaSymbolKind.VARIABLE).length;
}

/** Mana value on the stack, where X is the chosen value (CR 202.3b). */
export function manaValueWithX(cost: ManaCost, x: number): number {
  return manaValue(cost) + x * variableCount(cost);
}

/** CR 202.2: the colors of the colored mana symbols in the cost. */
export function costColors(cost: ManaCost): Color {
  let result: Color = Color.NONE;
  for (const s of cost.symbols) result |= colorContribution(s);
  return result;
}

/** Total fixed generic mana, ignoring {X}. */
export function genericAmount(cost: ManaCost): number {
  return cost.symbols.reduce((t, s) => (s.kind === ManaSymbolKind.GENERIC ? t + s.generic : t), 0);
}

export function isEmpty(cost: ManaCost): boolean {
  return cost.symbols.length === 0;
}

export function costText(cost: ManaCost): string {
  return cost.symbols.map((s) => s.text).join("");
}

/** A canonical key: equal costs (by value, as Python compares them) have equal keys. */
export function costKey(cost: ManaCost): string {
  return cost.symbols.map((s) => `${s.kind}:${s.colors}:${s.generic}:${s.text}`).join(",");
}

// -- CR 601.2f cost arithmetic ------------------------------------------------

export function plus(cost: ManaCost, other: ManaCost): ManaCost {
  return { symbols: [...cost.symbols, ...other.symbols] };
}

/** Add `amount` generic mana (a cost increase). */
export function increasedBy(cost: ManaCost, amount: number): ManaCost {
  if (amount <= 0) return cost;
  return { symbols: [...cost.symbols, ...genericCost(amount).symbols] };
}

/**
 * Reduce the generic part by `amount`, floored at zero. Never touches colored,
 * hybrid, monocolored hybrid or Phyrexian symbols (CR 601.2f). {X} must
 * already be substituted.
 */
export function reducedBy(cost: ManaCost, amount: number): ManaCost {
  if (amount <= 0) return cost;
  let remaining = amount;
  const out: ManaSymbol[] = [];
  for (const s of cost.symbols) {
    if (remaining && s.kind === ManaSymbolKind.GENERIC) {
      const take = Math.min(remaining, s.generic);
      remaining -= take;
      const left = s.generic - take;
      if (left) out.push(symbol(ManaSymbolKind.GENERIC, { generic: left, text: `{${left}}` }));
    } else {
      out.push(s);
    }
  }
  return { symbols: out };
}

/** Index of a symbol a colored or colorless reduction can come off; exact colors before hybrids. */
function findMatching(symbols: readonly ManaSymbol[], kind: number, colors: Color): number | null {
  if (kind === ManaSymbolKind.COLORLESS) {
    const index = symbols.findIndex((s) => s.kind === ManaSymbolKind.COLORLESS);
    return index < 0 ? null : index;
  }
  for (const wanted of [
    ManaSymbolKind.COLORED,
    ManaSymbolKind.PHYREXIAN,
    ManaSymbolKind.HYBRID,
    ManaSymbolKind.MONOCOLOR_HYBRID,
  ]) {
    const index = symbols.findIndex((s) => s.kind === wanted && (s.colors & colors) !== 0);
    if (index >= 0) return index;
  }
  return null;
}

/**
 * Reduce by specific mana (CR 118.7b-g): a colored reduction with nothing of
 * that color to take comes off the generic part instead; snow and X reduce
 * generic.
 */
export function reducedByMana(cost: ManaCost, reduction: ManaCost): ManaCost {
  const remaining = [...cost.symbols];
  let genericReduction = 0;
  for (const s of reduction.symbols) {
    if (s.kind === ManaSymbolKind.GENERIC) {
      genericReduction += s.generic;
      continue;
    }
    if (s.kind === ManaSymbolKind.SNOW || s.kind === ManaSymbolKind.VARIABLE) {
      genericReduction += 1;
      continue;
    }
    const wanted = s.kind !== ManaSymbolKind.COLORLESS ? s.colors : Color.NONE;
    const index = findMatching(remaining, s.kind, wanted);
    if (index === null) genericReduction += 1;
    else remaining.splice(index, 1);
  }
  return reducedBy({ symbols: remaining }, genericReduction);
}

/** Replace every {X} with that much generic mana (CR 601.2b, 107.1b: never negative). */
export function substituteX(cost: ManaCost, x: number): ManaCost {
  if (!variableCount(cost)) return cost;
  const chosen = Math.max(0, x);
  const out: ManaSymbol[] = [];
  for (const s of cost.symbols) {
    if (s.kind === ManaSymbolKind.VARIABLE) {
      if (chosen > 0) out.push(symbol(ManaSymbolKind.GENERIC, { generic: chosen, text: `{${chosen}}` }));
    } else {
      out.push(s);
    }
  }
  return { symbols: out };
}
