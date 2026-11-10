/**
 * Mana value of a printed mana cost (CR 202.3). Ported from the mana-value
 * half of mana symbols in mtgfish/rules/cr100_game_concepts/cr106_mana.py;
 * the rest of that module (pools, payment) comes with the core rules.
 */

import { ColorBits, enumValue, type ManaCost, type ManaSymbol } from "../../ir";
import { Color } from "../../kernel/enums";

const KIND = {
  GENERIC: "ManaSymbolKind.GENERIC",
  VARIABLE: "ManaSymbolKind.VARIABLE",
  COLORED: "ManaSymbolKind.COLORED",
  HYBRID: "ManaSymbolKind.HYBRID",
  MONOCOLOR_HYBRID: "ManaSymbolKind.MONOCOLOR_HYBRID",
  PHYREXIAN: "ManaSymbolKind.PHYREXIAN",
  INFINITY: "ManaSymbolKind.INFINITY",
} as const;

/** One symbol's contribution to mana value. {X} is 0 off the stack. */
export function symbolManaValue(symbol: ManaSymbol): number {
  switch (symbol.kind) {
    case KIND.GENERIC:
    case KIND.MONOCOLOR_HYBRID:
      return symbol.generic ?? 0;
    case KIND.VARIABLE:
      return 0;
    case KIND.INFINITY:
      return 1_000_000;
    default:
      // Coloured, colourless, snow, hybrid, Phyrexian count one; the Un-set
      // half symbol rounds up to one so mana value stays an integer.
      return 1;
  }
}

export function manaValue(cost: ManaCost): number {
  return (cost.symbols ?? []).reduce((total, s) => total + symbolManaValue(s), 0);
}

/** Mana value on the stack, where X is the chosen value (CR 202.3b). */
export function manaValueWithX(cost: ManaCost, x: number): number {
  const variables = (cost.symbols ?? []).filter((s) => s.kind === KIND.VARIABLE).length;
  return manaValue(cost) + x * variables;
}

/** CR 202.2: the colours of the coloured symbols in the cost. */
export function manaCostColors(cost: ManaCost): Color {
  let result: Color = Color.NONE;
  for (const s of cost.symbols ?? []) {
    if (s.kind === KIND.GENERIC || s.kind === KIND.VARIABLE) continue;
    if (s.colors !== undefined) result |= colorFlag(s.colors);
  }
  return result;
}

function colorFlag(literal: string): Color {
  // "Color.WHITE|BLUE": decoded by the IR's own reader.
  return enumValue(literal) & (ColorBits.WHITE! | ColorBits.BLUE! | ColorBits.BLACK! | ColorBits.RED! | ColorBits.GREEN!);
}

export const ZERO_COST: ManaCost = Object.freeze({ $: "ManaCost", symbols: [] });
