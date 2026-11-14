/**
 * Costs and the CR 601.2f total cost. Ported from
 * mtgfish/rules/cr100_game_concepts/cr118_costs.py.
 *
 * A cost is a list of components, mana only one of them; all must be
 * payable before any is paid (CR 601.2h). CR 601.2f order: mana or
 * alternative cost, plus additional costs and increases, then reductions.
 * Costs come from the IR (`Cost`, `CostComponent`); the mana in them is
 * read with the parser's `rules/mana/cost.ts`.
 */

import { CostKindValues } from "../../ir";
import type { Cost, CostComponent, Value } from "../../ir";
import { Zone } from "../../kernel/enums";
import { flag, list, num } from "../ir_read";
import { fromIR, increasedBy, plus, reducedBy, reducedByMana, isEmpty, substituteX, variableCount, ZERO_COST, type ManaCost } from "../mana/cost";
import { CastError } from "../cr600_spells_and_abilities/cast_error";
import { effectResult } from "./numbers";

export const CostKind = CostKindValues as Readonly<Record<
  | "MANA" | "TAP_SELF" | "UNTAP_SELF" | "TAP_OTHER" | "UNTAP_OTHER" | "SACRIFICE" | "DISCARD"
  | "EXILE_FROM_GRAVEYARD" | "EXILE_FROM_HAND" | "EXILE_FROM_LIBRARY" | "RETURN_TO_HAND" | "PUT_ON_LIBRARY"
  | "MILL" | "REVEAL" | "EXILE_FROM_BATTLEFIELD" | "DISCARD_AT_RANDOM" | "EXILE_MATERIALS" | "PAY_LIFE"
  | "PAY_ENERGY" | "REMOVE_COUNTERS" | "PUT_COUNTERS" | "LOYALTY" | "UNATTACH" | "CHOOSE" | "UNPARSED",
  number
>>;

/**
 * Costs that consume something: floored at one, because a free one is an
 * infinite loop rather than a slightly wrong card.
 */
export const CONSUMING_COSTS: ReadonlySet<number> = new Set([
  CostKind.SACRIFICE, CostKind.DISCARD, CostKind.DISCARD_AT_RANDOM, CostKind.EXILE_FROM_BATTLEFIELD,
  CostKind.EXILE_FROM_GRAVEYARD, CostKind.EXILE_FROM_HAND, CostKind.EXILE_FROM_LIBRARY,
  CostKind.EXILE_MATERIALS, CostKind.RETURN_TO_HAND, CostKind.PUT_ON_LIBRARY, CostKind.MILL,
  CostKind.REMOVE_COUNTERS, CostKind.TAP_OTHER, CostKind.UNTAP_OTHER,
]);

export const EXILE_ZONES: ReadonlyMap<number, Zone> = new Map([
  [CostKind.EXILE_FROM_BATTLEFIELD, Zone.BATTLEFIELD],
  [CostKind.EXILE_FROM_HAND, Zone.HAND],
  [CostKind.EXILE_FROM_GRAVEYARD, Zone.GRAVEYARD],
  [CostKind.EXILE_FROM_LIBRARY, Zone.LIBRARY],
]);

export const componentKind = (c: CostComponent): number => num(c.kind);
export const isManaComponent = (c: CostComponent): boolean => componentKind(c) === CostKind.MANA;

/**
 * What a component charges, given its evaluated amount: consuming costs at
 * least one, loyalty signed (CR 107.7), the rest never negative (CR 107.1b).
 * An undeterminable amount makes the cost unpayable (CR 903.4f, 118.6).
 */
export function requiredAmount(component: CostComponent, evaluated: number, undeterminable = false): number {
  if (undeterminable) throw new CastError("unpayable cost: it refers to a number that cannot be determined");
  const kind = componentKind(component);
  if (CONSUMING_COSTS.has(kind)) return Math.max(1, evaluated);
  if (kind === CostKind.LOYALTY) return evaluated;
  return effectResult(evaluated);
}

export const components = (cost: Cost | null | undefined): readonly CostComponent[] => list(cost?.components);

/** All mana components combined. */
export function manaComponent(cost: Cost | null | undefined): ManaCost {
  let total = ZERO_COST;
  for (const c of components(cost)) if (isManaComponent(c)) total = plus(total, fromIR(c.mana));
  return total;
}

export const nonManaComponents = (cost: Cost | null | undefined): CostComponent[] =>
  components(cost).filter((c) => !isManaComponent(c));

export const requiresTapping = (cost: Cost | null | undefined): boolean =>
  components(cost).some((c) => componentKind(c) === CostKind.TAP_SELF);

export const costIsUnparsed = (cost: Cost | null | undefined): boolean =>
  components(cost).some((c) => componentKind(c) === CostKind.UNPARSED);

export const costIsFree = (cost: Cost | null | undefined): boolean => components(cost).length === 0;

/** CR 107.3a: an {X} or a [-X] in the cost, announced as it is activated. */
export function asksForX(cost: Cost | null | undefined): boolean {
  if (variableCount(manaComponent(cost)) > 0) return true;
  const pending: Value[] = components(cost).flatMap((c) => (c.amount ? [c.amount] : []));
  const X = 1; // ValueKind.X
  while (pending.length > 0) {
    const value = pending.pop()!;
    if (num(value.kind) === X) return true;
    pending.push(...list(value.operands));
  }
  return false;
}

/** The cost actually being paid, staged in CR 601.2f order (Python `TotalCost`). */
export interface TotalCost {
  base: ManaCost;
  additional: CostComponent[];
  increase: number;
  reduction: number;
  xValue: number;
  isAlternative: boolean;
  manaReduction: ManaCost;
  unpayable: boolean;
}

export function newTotalCost(fields: Partial<TotalCost> = {}): TotalCost {
  return {
    base: ZERO_COST,
    additional: [],
    increase: 0,
    reduction: 0,
    xValue: 0,
    isAlternative: false,
    manaReduction: ZERO_COST,
    unpayable: false,
    ...fields,
  };
}

/** Add a cost's mana to the base and its other components to the additional costs. */
export function addCost(total: TotalCost, cost: Cost | null | undefined): void {
  total.base = plus(total.base, manaComponent(cost));
  total.additional.push(...nonManaComponents(cost));
}

/** CR 118.6: unpayable unless an alternative cost replaced it. */
export const isPayable = (total: TotalCost): boolean => !total.unpayable || total.isAlternative;

/** The mana that must be paid: X, then increases, then reductions (CR 601.2f, 118.9d). */
export function finalMana(total: TotalCost): ManaCost {
  let cost = substituteX(total.base, total.xValue);
  cost = increasedBy(cost, total.increase);
  if (!isEmpty(total.manaReduction)) cost = reducedByMana(cost, total.manaReduction);
  return reducedBy(cost, total.reduction);
}

/** The whole thing as one payable cost: final mana plus the other components. */
export interface PayableCost {
  readonly mana: ManaCost;
  readonly components: readonly CostComponent[];
}

export const asPayable = (total: TotalCost): PayableCost => ({ mana: finalMana(total), components: total.additional });

/** A Cost from the IR as a payable cost (activation costs). */
export const payableOf = (cost: Cost | null | undefined): PayableCost => ({ mana: manaComponent(cost), components: nonManaComponents(cost) });

export const isOptional = (additional: { optional?: boolean }): boolean => flag(additional.optional);
