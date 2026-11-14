/**
 * Which mana abilities to activate to pay a cost (CR 601.2g) - solved, not
 * guessed. Ported from mtgfish/rules/cr600_spells_and_abilities/mana_plan.py.
 *
 * Every untapped source contributes its *options* (each mana ability, each
 * mode, each colour of "any colour") with what each would add; a small
 * search picks options that pay the cost, cheapest sources first, then drops
 * any activation the plan doesn't need. The same search answers "could this
 * be paid?", so the legal actions and the payment agree.
 *
 * This file is the game-independent part: options in, plan out. Listing a
 * player's options and carrying a plan out need the game - its
 * characteristics, activation and resolution - and go through `ManaPlanHost`,
 * which the rules session implements (see engine/status/parser.md).
 */

import type { Ability, Cost, Effect } from "../../ir";
import { CostKindValues, EffectKindValues } from "../../ir";
import { enumValue } from "../../ir/decode";
import { Color } from "../../kernel/enums";
import { isChoice, isGeneric, manaValue, ManaSymbolKind, type ManaCost } from "./cost";
import { findPayment } from "./payment";
import type { PaymentContext } from "./pool";
import {
  addMana,
  amountOfColor,
  cloneManaPool,
  manaKindKey,
  poolTotal,
  removeMana,
  type ManaKind,
  type ManaPool,
} from "./pool";

/** How many partial plans the search may try before giving up (pathological boards only). */
export const SEARCH_BUDGET = 4000;

/** Drawbacks, cheapest first: a Karplusan Forest taps for {C} before {G} and a point of damage. */
export const Drawback = { FREE: 0, PAINFUL: 1, SACRIFICE: 2 } as const;

/** One way of activating one mana ability, and what it would add. */
export interface ManaOption {
  readonly source: number;
  readonly abilityIndex: number;
  readonly modes: readonly number[];
  /** The colour picked for an "add one mana of any color" effect. */
  readonly color: Color;
  readonly produces: readonly ManaKind[];
  /** The ability's own mana cost - a Signet's {1} - paid from the pool. */
  readonly manaCost: ManaCost | null;
  readonly drawback: number;
  /** The ability itself, so execution can check it is still the one planned. */
  readonly ability?: Ability;
}

/** A source's options in preference order: no drawback, no mana cost, more mana. */
export function sortSourceOptions(options: ManaOption[]): ManaOption[] {
  return [...options].sort(
    (a, b) =>
      a.drawback - b.drawback ||
      Number(a.manaCost !== null) - Number(b.manaCost !== null) ||
      b.produces.length - a.produces.length,
  );
}

// -- the search ---------------------------------------------------------------

/** The pool after activating `option`, or null if its own mana cost can't be paid. */
function apply(pool: ManaPool, option: ManaOption): ManaPool | null {
  const after = cloneManaPool(pool);
  if (option.manaCost !== null && option.manaCost.symbols.length > 0) {
    const payment = findPayment(after, option.manaCost);
    if (payment === null) return null;
    for (const [kind, amount] of payment.mana) removeMana(after, kind, amount);
  }
  for (const kind of option.produces) addMana(after, kind, 1);
  return after;
}

const SINGLE_COLORS = [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN];

/** Coloured symbols that admit exactly one colour, counted by colour. */
function colourNeeds(cost: ManaCost): Map<Color, number> {
  const needs = new Map<Color, number>();
  for (const s of cost.symbols) {
    if (isGeneric(s) || isChoice(s)) continue;
    const colors = SINGLE_COLORS.filter((c) => (s.colors & c) !== 0);
    if (colors.length === 1) {
      const c = colors[0] as Color;
      needs.set(c, (needs.get(c) ?? 0) + 1);
    }
  }
  return needs;
}

interface Ceiling {
  readonly total: number;
  readonly perColour: ReadonlyMap<Color, number>;
}

/** For each suffix of `sources`: the most mana, and of each colour, it can add (net of own costs). */
function ceilings(sources: readonly (readonly ManaOption[])[]): Ceiling[] {
  const out: Ceiling[] = [{ total: 0, perColour: new Map() }];
  for (let i = sources.length - 1; i >= 0; i--) {
    const options = sources[i] as readonly ManaOption[];
    const next = out[0] as Ceiling;
    const best = Math.max(
      ...options.map((o) => o.produces.length - (o.manaCost === null ? 0 : manaValue(o.manaCost))),
    );
    const colours = new Map(next.perColour);
    const seen = new Set<Color>();
    for (const o of options) for (const k of o.produces) if (k.color) seen.add(k.color);
    for (const colour of seen) {
      const most = Math.max(...options.map((o) => o.produces.filter((k) => k.color === colour).length));
      colours.set(colour, (colours.get(colour) ?? 0) + most);
    }
    out.unshift({ total: next.total + Math.max(0, best), perColour: colours });
  }
  return out;
}

/** Phyrexian symbols may be paid with life instead (CR 107.4f). */
function lifePayable(cost: ManaCost): number {
  return cost.symbols.filter((s) => s.kind === ManaSymbolKind.PHYREXIAN).length;
}

function canStillReach(pool: ManaPool, cost: ManaCost, needs: Map<Color, number>, ceiling: Ceiling): boolean {
  if (poolTotal(pool) + ceiling.total < manaValue(cost) - lifePayable(cost)) return false;
  for (const [colour, count] of needs) {
    if (amountOfColor(pool, colour) + (ceiling.perColour.get(colour) ?? 0) < count) return false;
  }
  return true;
}

function producesKey(option: ManaOption): string {
  return option.produces.map(manaKindKey).join(";");
}

export interface PlanInput {
  /** The player's pool now. */
  readonly pool: ManaPool;
  /** The player's life total; one point is always kept back (Python: life - 1). */
  readonly life: number;
  readonly cost: ManaCost;
  /** One list per source, of its alternatives; a source is activated at most once. */
  readonly sources: readonly (readonly ManaOption[])[];
  readonly context?: PaymentContext | null;
}

/**
 * The mana abilities to activate so `cost` can be paid, or null if none do.
 * An empty list means the pool already covers it. Deterministic: the first
 * plan found, trying cheap single-purpose sources first.
 */
export function planPayment(input: PlanInput): ManaOption[] | null {
  const { cost } = input;
  const context = input.context ?? null;
  const life = input.life - 1;
  const start = cloneManaPool(input.pool);
  const pays = (pool: ManaPool) => findPayment(pool, cost, { lifeAvailable: life, context }) !== null;
  if (pays(start)) return [];
  if (input.sources.length === 0) return null;

  // Cheap, dependable sources first; a flexible one is saved for the colour nothing else makes.
  const sources = [...input.sources]
    .filter((opts) => opts.length > 0)
    .sort((a, b) => {
      const da = Math.min(...a.map((o) => o.drawback));
      const db = Math.min(...b.map((o) => o.drawback));
      if (da !== db) return da - db;
      const va = new Set(a.map(producesKey)).size;
      const vb = new Set(b.map(producesKey)).size;
      if (va !== vb) return va - vb;
      return (a[0] as ManaOption).source - (b[0] as ManaOption).source;
    });
  const limits = ceilings(sources);
  const needs = colourNeeds(cost);
  let budget = SEARCH_BUDGET;
  const chosen: ManaOption[] = [];

  const search = (index: number, current: ManaPool): boolean => {
    budget -= 1;
    if (budget < 0) return false;
    if (pays(current)) return true;
    if (index === sources.length || !canStillReach(current, cost, needs, limits[index] as Ceiling)) return false;
    for (const option of sources[index] as readonly ManaOption[]) {
      const after = apply(current, option);
      if (after === null) continue;
      chosen.push(option);
      if (search(index + 1, after)) return true;
      chosen.pop();
    }
    return search(index + 1, current);
  };

  if (!search(0, start)) return null;
  return trimmed(chosen, start, cost, life, context);
}

/** Drop every activation the plan does not need (the search may have tapped Forests on the way to the Mountain). */
function trimmed(
  plan: ManaOption[],
  start: ManaPool,
  cost: ManaCost,
  life: number,
  context: PaymentContext | null,
): ManaOption[] {
  let current = [...plan];
  for (let index = current.length - 1; index >= 0; index--) {
    const candidate = [...current.slice(0, index), ...current.slice(index + 1)];
    let pool: ManaPool | null = start;
    for (const option of candidate) {
      pool = apply(pool, option);
      if (pool === null) break;
    }
    if (pool !== null && findPayment(pool, cost, { lifeAvailable: life, context }) !== null) current = candidate;
  }
  return current;
}

// -- drawbacks, read from the IR ----------------------------------------------

function walk(effect: Effect): Effect[] {
  return [effect, ...(effect.children ?? []).flatMap(walk), ...(effect.otherwise ?? []).flatMap(walk)];
}

/** The modal instruction among these effects (CR 700.2), or null. */
export function modalEffect(effects: readonly Effect[]): Effect | null {
  for (const effect of effects) {
    for (const node of walk(effect)) {
      if (enumValue(node.kind ?? -1) === EffectKindValues.CHOOSE_MODE && (node.children ?? []).length > 0) return node;
    }
  }
  return null;
}

/** A cost's drawback: sacrificing is worse than paying life, which is worse than nothing. */
export function costDrawback(cost: Cost | undefined): number {
  let worst: number = Drawback.FREE;
  for (const component of cost?.components ?? []) {
    const kind = enumValue(component.kind);
    if (kind === CostKindValues.SACRIFICE) worst = Math.max(worst, Drawback.SACRIFICE);
    else if (kind === CostKindValues.PAY_LIFE) worst = Math.max(worst, Drawback.PAINFUL);
  }
  return worst;
}

/** A pain land's damage rides on the effect, not the cost. */
export function effectDrawback(ability: Ability, modes: readonly number[]): number {
  let effects: readonly Effect[] = ability.effects ?? [];
  const modal = modalEffect(effects);
  const chosen = modes.length > 0 ? modal?.children?.[modes[0] as number] : undefined;
  if (modal !== null && chosen !== undefined) effects = effects.map((e) => (e === modal ? chosen : e));
  const hurts = new Set([EffectKindValues.DAMAGE, EffectKindValues.LOSE_LIFE]);
  return effects.some((e) => walk(e).some((n) => hurts.has(enumValue(n.kind ?? -1))))
    ? Drawback.PAINFUL
    : Drawback.FREE;
}

/** An option's drawback: the worse of its cost's and its chosen effects'. */
export function optionDrawback(ability: Ability, modes: readonly number[]): number {
  return Math.max(costDrawback(ability.cost), effectDrawback(ability, modes));
}

// -- the game side -------------------------------------------------------------

/**
 * What planning needs from the game; implemented by the rules session.
 * Python: mana_options (with _can_pay_activation, activation_mana_cost,
 * _outcomes / _predict) and execute_plan (with _pay_activation,
 * choose_modes, resolve_mana_ability, resolve_mana_triggers).
 */
export interface ManaPlanHost<G> {
  /**
   * Every untapped source the player could activate now, one list of
   * alternatives per source, each list sorted with `sortSourceOptions`.
   */
  manaOptions(game: G, player: number): ManaOption[][];
  /**
   * The planned ability if its source is still on the battlefield, still has
   * it (by identity, not just by index), and can still pay for it; else null.
   * This is the F61 guard.
   */
  stillActivatable(game: G, option: ManaOption): Ability | null;
  /** Pay the ability's cost and resolve it with the option's modes and colour (CR 605.3b). */
  activate(game: G, player: number, option: ManaOption, ability: Ability): void;
}

/** A planned mana source that can no longer be activated: the cast is rewound. */
export class PlanNoLongerHolds extends Error {
  override name = "PlanNoLongerHolds";
}

/**
 * Activate the planned mana abilities in order (CR 605.3b: no stack). Each
 * source is re-checked first (F61): an earlier activation resolves at once
 * and may have sacrificed this source to its cost, or ended the effect that
 * gave it the ability; the plan then no longer holds.
 */
export function executePlan<G>(host: ManaPlanHost<G>, game: G, player: number, plan: readonly ManaOption[]): void {
  for (const option of plan) {
    const ability = host.stillActivatable(game, option);
    if (ability === null) throw new PlanNoLongerHolds("a planned mana source can no longer be activated");
    host.activate(game, player, option, ability);
  }
}

/** Whether the pool plus the player's untapped sources can pay `cost`. */
export function canProduce<G>(
  host: ManaPlanHost<G>,
  game: G,
  player: number,
  input: Omit<PlanInput, "sources">,
): boolean {
  return planPayment({ ...input, sources: host.manaOptions(game, player) }) !== null;
}
