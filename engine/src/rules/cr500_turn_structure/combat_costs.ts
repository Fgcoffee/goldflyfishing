/**
 * Costs to attack and to block (CR 508.1c-d, 508.1h-j, 509.1b-f). Ported
 * from mtgfish/rules/cr500_turn_structure/combat_costs.py.
 *
 * "Creatures can't attack you unless their controller pays {2} for each
 * creature they control that's attacking you" is a restriction a cost lifts
 * (CR 508.1c). The cost is part of declaring attackers: the active player
 * determines the total (508.1h), may activate mana abilities (508.1i), and
 * pays it all at once (508.1j). Blocking is the same (509.1d-f).
 *
 * Read by the parser as an UNLESS_PAYS whose children are RESTRICTION
 * effects on attacking or blocking, inside a static ability - optionally
 * behind an "as long as" gate. Three per-creature amounts are understood: a
 * flat cost, one "for each of those creatures" (charged once per creature
 * covered), and one "for each" of something independent of the combat.
 * `taxRefusal` names every other shape so the parser refuses the card.
 *
 * CR 508.1d / 509.1c: a player is never required to pay a cost to attack or
 * block, so a requirement never forces a creature that would have to pay.
 */

import { complete } from "../../ir";
import type { Ability, Condition, Cost, Effect, ObjectFilter, Restriction, Value } from "../../ir";
import { agentFor, logRecord, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { CostKind } from "../cr100_game_concepts/costs";
import { walk } from "../cr600_spells_and_abilities/effect_props";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { canPayCost, payCost } from "../cr600_spells_and_abilities/paying";
import { flag, ir, irEqual, list, num, numSet } from "../ir_read";
import { costText, fromIR, isEmpty, manaValue, plus, variableCount, ZERO_COST, type ManaCost } from "../mana/cost";
import { Act, restrictionApplies } from "./restrictions";

const STATIC: number = ir("AbilityKind.STATIC");
const UNLESS_PAYS: number = ir("EffectKind.UNLESS_PAYS");
const RESTRICTION: number = ir("EffectKind.RESTRICTION");
const CONDITIONAL: number = ir("EffectKind.CONDITIONAL");
const ALWAYS: number = ir("ConditionKind.ALWAYS");
const AND: number = ir("ConditionKind.AND");
const PRODUCT: number = ir("ValueKind.PRODUCT");
const COUNT: number = ir("ValueKind.COUNT");
const YOU: number = ir("PlayerScope.YOU");
const CONTROLLER_OF: number = ir("PlayerScope.CONTROLLER_OF");
const ANY_CONTROLLER: number = ir("ControllerRelation.ANY");

/** The acts a combat tax can lift. */
// Read from the IR, not `Act`: restrictions.ts imports combat.ts, which imports this.
export const ATTACK_ACTS: ReadonlySet<number> = new Set([ir("Act.ATTACK"), ir("Act.ATTACK_PLAYER"), ir("Act.ATTACK_PLANESWALKER")]);
export const COMBAT_ACTS: ReadonlySet<number> = new Set([...ATTACK_ACTS, ir("Act.BLOCK")]);

/** One "can't attack/block unless [cost]" in force. */
export interface CombatTax {
  /** The restrictions the payment lifts, with source, controller and gate filled in. */
  readonly restrictions: readonly Restriction[];
  /** The cost for one creature covered, before any scale. */
  readonly mana: ManaCost;
  /** A multiplier that is not the creatures themselves; null: once per creature covered. */
  readonly scale: Value | null;
  readonly source: ObjectId;
  readonly controller: PlayerId;
  readonly text: string;
}

type Pair = [Restriction, Effect];

const isAlways = (c: Condition | null | undefined): boolean => c == null || num(c.kind) === ALWAYS;
/** Python dataclass equality: defaults filled in, then compared. */
const sameFilter = (a: ObjectFilter, b: ObjectFilter): boolean => irEqual(complete(a), complete(b));

// -- what the engine can charge

function isCombatTax(effect: Effect): boolean {
  const children = list(effect.children);
  return num(effect.kind) === UNLESS_PAYS && children.length > 0 && children.some((c) => num(c.kind) === RESTRICTION);
}

/** Each combat tax in a static ability, with the "as long as" gating it. */
function* gatedTaxes(effects: readonly Effect[], gate: Condition): Generator<[Effect, Condition]> {
  for (const effect of effects) {
    if (isCombatTax(effect)) {
      yield [effect, gate];
    } else if (num(effect.kind) === CONDITIONAL && list(effect.otherwise).length === 0) {
      let inner: Condition = effect.condition ?? { $: "Condition" };
      if (!isAlways(gate)) inner = { $: "Condition", kind: AND, operands: [gate, inner], text: "and" };
      yield* gatedTaxes(list(effect.children), inner);
    }
  }
}

/**
 * Why the engine could not charge a combat tax in `ability`, or null. The
 * parser asks this of every ability it reads.
 */
export function taxRefusal(ability: Ability): string | null {
  const taxes: Effect[] = [];
  for (const effect of list(ability.effects)) for (const node of walk(effect)) if (isCombatTax(node)) taxes.push(node);
  if (taxes.length === 0) return null;
  if (num(ability.kind) !== STATIC) return "a cost to attack or block outside a static ability";
  const reachable = new Set<Effect>();
  for (const [effect] of gatedTaxes(list(ability.effects), { $: "Condition" })) reachable.add(effect);
  for (const node of taxes) {
    if (!reachable.has(node)) return "a cost to attack or block nested where it is not continuous";
    const reason = shapeRefusal(node);
    if (reason) return reason;
  }
  return null;
}

function pairsOf(effect: Effect): Pair[] {
  const out: Pair[] = [];
  for (const child of list(effect.children)) for (const r of list(child.restrictions)) out.push([r, child]);
  return out;
}

function shapeRefusal(effect: Effect): string | null {
  if (!list(effect.children).every((c) => num(c.kind) === RESTRICTION)) {
    return "a cost to attack or block guarding something else as well";
  }
  const restrictions = pairsOf(effect);
  if (restrictions.length === 0 || restrictions.some(([r]) => !COMBAT_ACTS.has(num(r.act)))) {
    return "a cost lifting a restriction other than attacking or blocking";
  }
  const cost = effect.pay_cost;
  if (cost == null || list(cost.choices).length > 0 || list(cost.components).length !== 1) {
    return "a combat cost the engine cannot charge";
  }
  const component = list(cost.components)[0]!;
  const mana = fromIR(component.mana);
  if (num(component.kind) !== CostKind.MANA || isEmpty(mana)) return "a combat cost other than mana";
  if (variableCount(mana)) return "a combat cost with an unbound {X}";
  const payer = effect.players;
  if (payer == null) return "a combat cost with no payer";
  if (num(payer.scope) === YOU) {
    // "This creature can't attack unless you pay {2}": the payer declares it.
    if (restrictions.some(([r, child]) => !flag(subjectOf(r, child).source_only))) {
      return "a combat cost paid by someone other than the declaring player";
    }
  } else if (!(num(payer.scope) === CONTROLLER_OF && !payer.reference)) {
    return "a combat cost paid by someone other than the declaring player";
  }
  if (component.scale != null && scaleKind(component.scale, restrictions) === null) {
    return "a combat cost scaled by something the engine cannot count";
  }
  return null;
}

function subjectOf(restriction: Restriction, child: Effect): ObjectFilter {
  return restriction.subject ?? child.targets ?? { $: "ObjectFilter", source_only: true };
}

const PER_CREATURE = "per creature";
const INDEPENDENT = "independent";
/** A product of one per-creature factor and independent ones. */
const PRODUCT_KIND = "per creature, times";

/** The multiplier left once the per-creature factor is charged by counting creatures. */
function independentPart(scale: Value, restrictions: readonly Pair[]): Value | null {
  const kind = scaleKind(scale, restrictions);
  if (kind === PER_CREATURE) return null;
  if (kind === INDEPENDENT) return scale;
  const rest = list(scale.operands).filter((operand) => scaleKind(operand, restrictions) !== PER_CREATURE);
  return rest.length === 1 ? rest[0]! : { ...scale, operands: rest.slice() };
}

/** How a "for each" on a combat cost multiplies it; null when it cannot be counted. */
function scaleKind(scale: Value, restrictions: readonly Pair[]): string | null {
  const constant = num(scale.constant);
  if (num(scale.kind) === PRODUCT && (constant === 0 || constant === 1)) {
    const kinds = list(scale.operands).map((operand) => scaleKind(operand, restrictions));
    if (kinds.includes(null) || kinds.filter((k) => k === PER_CREATURE).length > 1) return null;
    return kinds.includes(PER_CREATURE) ? PRODUCT_KIND : INDEPENDENT;
  }
  const filt = scale.filter;
  if (num(scale.kind) === COUNT && filt != null && list(scale.operands).length === 0) {
    if (flag(filt.remembered)) {
      const stripped: ObjectFilter = { ...filt, remembered: false };
      return restrictions.every(([r, c]) => sameFilter(stripped, subjectOf(r, c))) ? PER_CREATURE : null;
    }
    const acts = new Set(restrictions.map(([r]) => num(r.act)));
    const allAttack = [...acts].every((a) => ATTACK_ACTS.has(a));
    const onlyBlock = acts.size === 1 && acts.has(Act.BLOCK);
    const inCombat = allAttack ? filt.attacking : onlyBlock ? filt.blocking : null;
    if (inCombat) {
      const stripped: ObjectFilter = {
        ...filt,
        attacking: null,
        blocking: null,
        controller: ANY_CONTROLLER as never,
        controller_specific: null,
      };
      return restrictions.every(([r, c]) => sameFilter(stripped, subjectOf(r, c))) ? PER_CREATURE : null;
    }
  }
  if (mentionsCombat(scale)) return null;
  return INDEPENDENT;
}

function mentionsCombat(value: Value): boolean {
  const filt = value.filter;
  if (filt != null && (flag(filt.remembered) || filt.attacking != null || filt.blocking != null || filt.blocked != null)) return true;
  return list(value.operands).some(mentionsCombat);
}

// -- the taxes in force

/** Every combat tax in force, rebuilt from live static abilities (CR 611.3). */
export function combatTaxes(game: Game): CombatTax[] {
  const out: CombatTax[] = [];
  for (const objectId of game.battlefield.slice()) {
    const obj = game.objects.get(objectId);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      if (!numSet(ability.functions_in, [2]).has(obj.zone)) continue;
      for (const [effect, gate] of gatedTaxes(list(ability.effects), ability.static_condition ?? { $: "Condition" })) {
        // The parser refuses these; a hand-built one is not charged as something else.
        if (shapeRefusal(effect) !== null) continue;
        const component = list(effect.pay_cost!.components)[0]!;
        const restrictions = pairsOf(effect);
        let scale = component.scale ?? null;
        if (scale !== null) scale = independentPart(scale, restrictions);
        out.push({
          restrictions: restrictions.map(([r, child]) => ({
            $: "Restriction",
            act: r.act,
            subject: r.subject ?? child.targets ?? null,
            players: r.players ?? child.players ?? null,
            source: obj.id,
            controller: obj.controller,
            condition: gate,
            counterpart: r.counterpart ?? null,
            text: r.text || child.text || "",
          })),
          mana: fromIR(component.mana),
          scale,
          source: obj.id,
          controller: obj.controller,
          text: ability.text ?? "",
        });
      }
    }
  }
  return out;
}

function times(mana: ManaCost, count: number): ManaCost {
  const symbols = [];
  for (let i = 0; i < Math.max(0, count); i += 1) symbols.push(...mana.symbols);
  return { symbols };
}

function charge(game: Game, tax: CombatTax): ManaCost {
  if (tax.scale === null) return tax.mana;
  return times(tax.mana, matchHooks().evaluate(game, tax.scale, { source: tax.source, controller: tax.controller }));
}

/** What attacking `player` (or the `permanent` they defend) with `obj` costs (CR 508.1h). */
export function attackCost(game: Game, obj: GameObject, player: PlayerId, permanent: ObjectId = NO_OBJECT): ManaCost {
  let total = ZERO_COST;
  const attacked = permanent !== NO_OBJECT ? (game.objects.get(permanent) ?? null) : null;
  for (const tax of combatTaxes(game)) {
    for (const restriction of tax.restrictions) {
      const act = num(restriction.act);
      let hit = false;
      if (act === Act.ATTACK) hit = restrictionApplies(game, restriction, Act.ATTACK, { obj });
      else if (act === Act.ATTACK_PLAYER) hit = attacked === null && restrictionApplies(game, restriction, Act.ATTACK_PLAYER, { obj, player });
      else if (act === Act.ATTACK_PLANESWALKER) {
        hit = attacked !== null && restrictionApplies(game, restriction, Act.ATTACK_PLANESWALKER, { obj, counterpart: attacked });
      }
      if (hit) {
        // One restriction lifted is one cost: a creature attacks one thing.
        total = plus(total, charge(game, tax));
        break;
      }
    }
  }
  return total;
}

/** What blocking with `obj` costs (CR 509.1d): once per blocker, however many it blocks. */
export function blockCost(game: Game, obj: GameObject): ManaCost {
  let total = ZERO_COST;
  for (const tax of combatTaxes(game)) {
    if (tax.restrictions.some((r) => num(r.act) === Act.BLOCK && restrictionApplies(game, r, Act.BLOCK, { obj }))) {
      total = plus(total, charge(game, tax));
    }
  }
  return total;
}

// -- paying

function asCost(mana: ManaCost): Cost {
  return {
    $: "Cost",
    components: [
      {
        $: "CostComponent",
        kind: CostKind.MANA as never,
        mana: {
          $: "ManaCost",
          symbols: mana.symbols.map((s) => ({ $: "ManaSymbol", kind: s.kind as never, colors: s.colors as never, generic: s.generic, text: s.text })),
        },
      },
    ],
  };
}

interface CostChooser {
  chooseCombatCosts?(game: Game, player: PlayerId, costs: Map<ObjectId, ManaCost>): Iterable<ObjectId> | null;
}

/**
 * The taxed creatures the player will pay for: `costs` with the most
 * expensive dropped until the total can be paid. `tapped` will be tapped by
 * the declaration itself (CR 508.1f), so their mana is not counted on. The
 * agent may decline any of it (`chooseCombatCosts`).
 */
export function affordable(
  game: Game,
  player: PlayerId,
  costs: ReadonlyMap<ObjectId, ManaCost>,
  options: { tapped?: readonly ObjectId[] } = {},
): Map<ObjectId, ManaCost> {
  let chosen = new Map([...costs].filter(([, mana]) => !isEmpty(mana)));
  const agent = agentFor(game, player) as CostChooser | null | undefined;
  if (agent?.chooseCombatCosts !== undefined && chosen.size > 0) {
    const willing = new Set(agent.chooseCombatCosts(game, player, new Map(chosen)) ?? []);
    chosen = new Map([...chosen].filter(([k]) => willing.has(k)));
  }
  const flipped: GameObject[] = [];
  for (const objectId of options.tapped ?? []) {
    const obj = game.objects.get(objectId);
    if (obj !== undefined && !obj.tapped) {
      obj.tapped = true;
      flipped.push(obj);
    }
  }
  try {
    while (chosen.size > 0) {
      if (canPayCost(game, player, asCost(totalOf(chosen)))) break;
      let dearest: ObjectId | null = null;
      for (const [k, v] of chosen) {
        if (dearest === null) {
          dearest = k;
          continue;
        }
        const best = manaValue(chosen.get(dearest)!);
        const value = manaValue(v);
        if (value > best || (value === best && k > dearest)) dearest = k;
      }
      chosen.delete(dearest!);
    }
  } finally {
    for (const obj of flipped) obj.tapped = false;
  }
  return chosen;
}

function totalOf(costs: ReadonlyMap<ObjectId, ManaCost>): ManaCost {
  let total = ZERO_COST;
  for (const id of [...costs.keys()].sort((a, b) => a - b)) total = plus(total, costs.get(id)!);
  return total;
}

/** CR 508.1i-j / 509.1e-f: pay the locked-in total, all or nothing. */
export function pay(game: Game, player: PlayerId, costs: ReadonlyMap<ObjectId, ManaCost>, what: string): boolean {
  const total = totalOf(costs);
  if (isEmpty(total)) return true;
  if (!payCost(game, player, asCost(total))) return false;
  logRecord(game, `player ${player} pays ${costText(total)} to ${what}`, { kind: "cost" });
  return true;
}
