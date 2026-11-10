/**
 * Paying a mana cost from a pool (CR 601.2h). Ported from Payment,
 * find_payment, can_pay, _iter_branches and _assign_specific in
 * mtgfish/rules/cr100_game_concepts/cr106_mana.py.
 *
 * A real constraint solve, not a greedy match: {W/U}{W/U} from one white and
 * one blue succeeds. Deterministic - the same pool and cost always give the
 * same payment - because replay reconstructs a game from its seed.
 */

import {
  accepts,
  isChoice,
  isEmpty,
  isGeneric,
  lifeCost,
  ManaSymbolKind,
  substituteX,
  type ManaCost,
  type ManaSymbol,
} from "./cost";
import { compareKinds, kindUsableFor, type ManaKind, type ManaPool, type PaymentContext } from "./pool";

/** A concrete, legal way to pay a mana cost. */
export interface Payment {
  /** How much of each kind of mana to spend, in canonical kind order. */
  readonly mana: readonly (readonly [ManaKind, number])[];
  /** Life paid for Phyrexian symbols (CR 107.4f). */
  readonly life: number;
}

export function paymentTotalMana(payment: Payment): number {
  return payment.mana.reduce((total, [, n]) => total + n, 0);
}

export interface PaymentOptions {
  readonly xValue?: number;
  readonly lifeAvailable?: number;
  /** What is being paid for, for mana restrictions (CR 106.6). */
  readonly context?: PaymentContext | null;
}

/** Choice assignments, fewest alternatives first: one mana before two, mana before life. */
function* branches(n: number): Generator<number[]> {
  if (n === 0) {
    yield [];
    return;
  }
  const masks = Array.from({ length: 1 << n }, (_, m) => m);
  const popcount = (m: number) => {
    let c = 0;
    for (let v = m; v; v &= v - 1) c++;
    return c;
  };
  masks.sort((a, b) => popcount(a) - popcount(b) || a - b);
  for (const mask of masks) yield Array.from({ length: n }, (_, i) => (mask >> i) & 1);
}

/** One unit of mana to each symbol, by backtracking, most-constrained symbol first. */
function assignSpecific(symbols: ManaSymbol[], kinds: ManaKind[], counts: number[], spend: number[]): boolean {
  if (symbols.length === 0) return true;
  const candidates: [number, number[]][] = [];
  for (let si = 0; si < symbols.length; si++) {
    const sym = symbols[si] as ManaSymbol;
    const matches: number[] = [];
    for (let ki = 0; ki < kinds.length; ki++) if (accepts(sym, kinds[ki] as ManaKind)) matches.push(ki);
    if (matches.length === 0) return false;
    candidates.push([si, matches]);
  }
  candidates.sort((a, b) => a[1].length - b[1].length || a[0] - b[0]);

  const recurse = (pos: number): boolean => {
    if (pos === candidates.length) return true;
    const [, matches] = candidates[pos] as [number, number[]];
    for (const ki of matches) {
      if ((counts[ki] as number) - (spend[ki] as number) <= 0) continue;
      spend[ki] = (spend[ki] as number) + 1;
      if (recurse(pos + 1)) return true;
      spend[ki] = (spend[ki] as number) - 1;
    }
    return false;
  };
  return recurse(0);
}

/**
 * A legal payment for `cost` from `pool`, or null. Prefers mana over life
 * for Phyrexian symbols and the colored half of {2/W}; choosing *well* is
 * the AI's job.
 */
export function findPayment(pool: ManaPool, cost: ManaCost, options: PaymentOptions = {}): Payment | null {
  if (isEmpty(cost)) return { mana: [], life: 0 };
  const context = options.context ?? null;
  const lifeAvailable = options.lifeAvailable ?? 0;

  // Mana whose restriction this payment doesn't satisfy is unavailable (CR 106.6).
  const usable = [...pool.buckets.values()]
    .filter((b) => kindUsableFor(b.kind, context))
    .sort((a, b) => compareKinds(a.kind, b.kind));
  const kinds = usable.map((b) => b.kind);
  const counts = usable.map((b) => b.count);

  const symbols = substituteX(cost, options.xValue ?? 0).symbols;
  const specific = symbols.filter((s) => !isGeneric(s));
  const genericNeeded = symbols.reduce((t, s) => (s.kind === ManaSymbolKind.GENERIC ? t + s.generic : t), 0);

  const choicePoints: number[] = [];
  specific.forEach((s, i) => {
    if (isChoice(s)) choicePoints.push(i);
  });
  if (choicePoints.length > 12) throw new RangeError(`unreasonable number of hybrid symbols in a cost`);

  for (const branch of branches(choicePoints.length)) {
    const spend = kinds.map(() => 0);
    let life = 0;
    let extraGeneric = 0;
    const mustMatch: ManaSymbol[] = [];

    specific.forEach((sym, idx) => {
      const point = choicePoints.indexOf(idx);
      // Branch bit 1 takes the alternative: life for Phyrexian, the generic half of {2/W}.
      if (point >= 0 && branch[point]) {
        if (sym.kind === ManaSymbolKind.PHYREXIAN) life += lifeCost(sym);
        else extraGeneric += sym.generic;
        return;
      }
      mustMatch.push(sym);
    });

    if (life > lifeAvailable) continue;
    if (!assignSpecific(mustMatch, kinds, counts, spend)) continue;

    let need = genericNeeded + extraGeneric;
    const remaining = counts.reduce((t, n, i) => t + n - (spend[i] as number), 0);
    if (remaining < need) continue;
    // Generic mana takes anything left, in canonical order so the result is reproducible.
    for (let i = 0; i < kinds.length && need > 0; i++) {
      const take = Math.min(need, (counts[i] as number) - (spend[i] as number));
      spend[i] = (spend[i] as number) + take;
      need -= take;
    }
    if (need > 0) continue;

    const mana: (readonly [ManaKind, number])[] = [];
    kinds.forEach((kind, i) => {
      if (spend[i]) mana.push([kind, spend[i] as number] as const);
    });
    return { mana, life };
  }
  return null;
}

export function canPay(pool: ManaPool, cost: ManaCost, options: PaymentOptions = {}): boolean {
  return findPayment(pool, cost, options) !== null;
}
