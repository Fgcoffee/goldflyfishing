/**
 * Mana value and colors of an IR ManaCost (CR 202.2, 202.3): the same rules
 * as ./cost.ts, for code holding the IR's cost objects (characteristics,
 * layers). Read through `fromIR`, so IR version 1 and 2 both work.
 */

import type { ManaCost as IRManaCost } from "../../ir";
import type { Color } from "../../kernel/enums";
import { costColors, fromIR, manaValue as costManaValue, manaValueWithX as costManaValueWithX } from "./cost";

export function manaValue(cost: IRManaCost): number {
  return costManaValue(fromIR(cost));
}

/** Mana value on the stack, where X is the chosen value (CR 202.3b). */
export function manaValueWithX(cost: IRManaCost, x: number): number {
  return costManaValueWithX(fromIR(cost), x);
}

/** CR 202.2: the colours of the coloured symbols in the cost. */
export function manaCostColors(cost: IRManaCost): Color {
  return costColors(fromIR(cost));
}

export const ZERO_COST: IRManaCost = Object.freeze({ $: "ManaCost", symbols: [] });
