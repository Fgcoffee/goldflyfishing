/**
 * The total cost of a spell (CR 601.2b, 601.2f, 118.8-9) and cost changes.
 * Ported from cr601_casting.py (chosen_alternative_cost through
 * cost_reductions), with the helpers it calls from
 * cr702_cost_keyword_rules.py (chosen_cost, chosen_mode_costs,
 * cheapest_mode_cost) and cr702_paid_costs.py (apply_emerge).
 */

import type { Ability, AdditionalCost, AlternativeCost, Cost, Effect } from "../../ir";
import { Zone } from "../../kernel/enums";
import { permanents, player, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import type { PlayerId } from "../../kernel/ids";
import type { Action } from "../cr100_game_concepts/priority";
import { commanderTax } from "../cr100_game_concepts/player";
import {
  addCost,
  CostKind,
  componentKind,
  manaComponent,
  newTotalCost,
  nonManaComponents,
  type TotalCost,
} from "../cr100_game_concepts/costs";
import { additionalCosts, alternativeCosts, charsManaValue, replaceCharacteristics, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { flag, ir, list, num, numSet, str } from "../ir_read";
import { fromIR, manaValue } from "../mana/cost";
import { commanderIdentity } from "../zones";
import { CastError } from "./cast_error";
import { checkPayable, choosePermanents } from "./cost_checks";
import { characteristicsOf, printedCharacteristics } from "./layers";
import { modalEffect } from "./modes";
import { spellEffects } from "./targeting";

const STATIC: number = ir("AbilityKind.STATIC");
const MODIFY_COST: number = ir("EffectKind.MODIFY_COST");
const GRANT_ABILITY: number = ir("EffectKind.GRANT_ABILITY");

/** CR 601.2b: the alternative cost chosen as the spell was proposed; at most one (CR 118.9a). */
export function chosenAlternativeCost(game: Game, spell: GameObject, act: Action): AlternativeCost | null {
  if (spell.alternativeCostOffered != null) return spell.alternativeCostOffered as AlternativeCost;
  // CR 702.37c: a face-down spell has no abilities; its morph cost is read from the card.
  const chars = spell.faceDown ? printedCharacteristics(game, spell) : characteristicsOf(game, spell);
  const available = alternativeCosts(chars) as AlternativeCost[];
  return act.alternativeCost >= 0 && act.alternativeCost < available.length ? available[act.alternativeCost]! : null;
}

/** Remember how the spell was paid for ("if its sneak cost was paid", CR 702.190b). */
export function recordAlternativeCost(game: Game, spell: GameObject, act: Action): void {
  const alternative = chosenAlternativeCost(game, spell, act);
  if (alternative === null) return;
  spell.alternativeCostPaid = str(alternative.keyword);
  if (flag(alternative.enters_tapped_and_attacking)) {
    spell.entersTappedAndAttacking = true;
    spell.entersAttackingLike = spell.costPaidObjects.slice();
  }
}

/** CR 601.2f: mana or alternative cost, plus additional costs and increases, then reductions. */
export function computeTotalCost(game: Game, spell: GameObject, playerId: PlayerId, act: Action, fromCommandZone = false): TotalCost {
  const chars = characteristicsOf(game, spell);
  const alternative = chosenAlternativeCost(game, spell, act);
  let total: TotalCost;
  if (alternative !== null && flag(alternative.pays_mana_cost)) {
    // CR 702.133a: the mana cost as usual, plus the permission's own cost.
    total = newTotalCost({ base: fromIR(chars.manaCost), xValue: act.xValue, unpayable: !chars.hasManaCost });
    addCost(total, alternative.cost);
  } else if (alternative !== null) {
    // CR 118.9c: replaces what is paid, not the spell's mana cost.
    total = newTotalCost({ base: manaComponent(alternative.cost), xValue: act.xValue, isAlternative: true });
    total.additional.push(...nonManaComponents(alternative.cost));
    if (str(alternative.keyword).toLowerCase() === "emerge") applyEmerge(game, spell, playerId, total);
  } else {
    // CR 118.6: no mana cost is unpayable, not zero.
    total = newTotalCost({ base: fromIR(chars.manaCost), xValue: act.xValue, unpayable: !chars.hasManaCost });
  }

  // CR 118.8a, 118.9d: additional costs, also on top of an alternative cost.
  const payments = recordedPayments(chars, act.additionalCosts);
  (additionalCosts(chars) as AdditionalCost[]).forEach((additional, index) => {
    for (let i = 0; i < timesCharged(spell, additional, index, payments); i += 1) {
      addCost(total, chosenCost(game, playerId, additional.cost, spell));
    }
  });
  // CR 702.172a: each chosen spree mode adds its cost.
  for (const cost of chosenModeCosts(game, spell)) addCost(total, cost);

  if (fromCommandZone && spell.isCommander) {
    total.increase += commanderTax(player(game, playerId), commanderIdentity(game, spell.id)); // CR 903.8
  }
  for (const amount of costIncreases(game, spell, playerId)) total.increase += amount;
  for (const amount of costReductions(game, spell, playerId)) total.reduction += amount;
  return total;
}

/** Optional additional costs that may be paid any number of times (CR 702.56a, 702.33c, 702.157a). */
const PAID_ANY_NUMBER_OF_TIMES = new Set(["replicate", "multikicker", "squad"]);

/** The optional additional costs paid, one index per payment. */
export function recordedPayments(chars: Characteristics, announced: readonly number[]): number[] {
  const costs = additionalCosts(chars) as AdditionalCost[];
  const out: number[] = [];
  for (const index of announced) {
    if (index < 0 || index >= costs.length || !flag(costs[index]!.optional)) continue;
    const repeatable = PAID_ANY_NUMBER_OF_TIMES.has(str(costs[index]!.keyword).toLowerCase());
    if (out.includes(index) && !repeatable) continue;
    out.push(index);
  }
  return out;
}

function timesCharged(spell: GameObject, additional: AdditionalCost, index: number, payments: readonly number[]): number {
  if (str(additional.keyword).toLowerCase() === "escalate") return Math.max(0, spell.chosenModes.length - 1); // CR 702.120a
  if (!flag(additional.optional)) return 1;
  return payments.filter((i) => i === index).length;
}

/**
 * The cost paid when a cost is written as a choice (CR 702.166a, bargain):
 * the first choice whose components can all be paid, else the first.
 */
export function chosenCost(game: Game, playerId: PlayerId, cost: Cost, spell: GameObject): Cost {
  const choices = list(cost.choices);
  if (choices.length === 0) return cost;
  for (const option of choices) {
    try {
      for (const component of nonManaComponents(option)) checkPayable(game, playerId, component, spell, { spell });
    } catch (e) {
      if (e instanceof CastError) continue;
      throw e;
    }
    return option;
  }
  return choices[0]!;
}

/** CR 702.172a: the costs of the spree modes chosen for this spell. */
export function chosenModeCosts(game: Game, spell: GameObject): Cost[] {
  const modal = modalEffect(spellEffects(game, spell));
  const costs = list(modal?.mode_costs) as Cost[];
  if (costs.length === 0) return [];
  return spell.chosenModes.filter((i) => i >= 0 && i < costs.length).map((i) => costs[i]!);
}

/** The least a spree spell can add on top of its mana cost: its cheapest mode; null otherwise. */
export function cheapestModeCost(game: Game, obj: GameObject): Cost | null {
  const costs = list(modalEffect(spellEffects(game, obj))?.mode_costs) as Cost[];
  if (costs.length === 0) return null;
  let best = costs[0]!;
  for (const c of costs) if (manaValue(manaComponent(c)) < manaValue(manaComponent(best))) best = c;
  return best;
}

/** CR 702.119a, c: the creature to sacrifice for emerge is chosen now, and reduces the cost. */
export function applyEmerge(game: Game, spell: GameObject, playerId: PlayerId, total: TotalCost): void {
  const index = total.additional.findIndex((c) => componentKind(c) === CostKind.SACRIFICE && c.filter != null);
  if (index < 0) return;
  const component = total.additional[index]!;
  const [victim] = choosePermanents(game, playerId, component, 1, spell);
  total.additional[index] = { ...component, filter: { $: "ObjectFilter", specific: [victim!.id] } };
  total.reduction += charsManaValue(characteristicsOf(game, victim!));
}

/** Whether a cost change applies to the spell being cast: its filter with the zone dropped. */
function matchesBeingCast(game: Game, spell: GameObject, effect: Effect, obj: GameObject): boolean {
  if (effect.targets == null) return true;
  const spec = { ...effect.targets, zones: { $set: [] } };
  return matchHooks().matches(game, spell, spec, { source: obj.id, controller: obj.controller });
}

function staticConditionHolds(game: Game, ability: Ability, obj: GameObject): boolean {
  const condition = ability.static_condition;
  if (condition === undefined || num(condition.kind) === 0) return true;
  return matchHooks().holds(game, condition, { source: obj.id, controller: obj.controller });
}

/**
 * `chars` with the abilities the card will have as a spell (CR 601.2a-b):
 * grants aimed at spells on the stack, read with the zone dropped.
 */
export function asItWouldBeCast(game: Game, card: GameObject, chars: Characteristics): Characteristics {
  if (card.zone === Zone.STACK) return chars;
  const granted: Ability[] = [];
  for (const obj of permanents(game)) {
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      for (const effect of list(ability.effects)) {
        if (num(effect.kind) !== GRANT_ABILITY || effect.targets == null) continue;
        if (!numSet(effect.targets.zones, [Zone.BATTLEFIELD]).has(Zone.STACK)) continue;
        if (!matchesBeingCast(game, card, effect, obj)) continue;
        if (!staticConditionHolds(game, ability, obj)) continue;
        granted.push(...list(effect.granted_abilities));
        for (const word of list(effect.keywords)) granted.push({ $: "Ability", kind: STATIC as never, keyword: word, text: word });
      }
    }
  }
  return granted.length === 0 ? chars : replaceCharacteristics(chars, { abilities: [...chars.abilities, ...granted] });
}

/**
 * Every cost change that applies to this spell, signed (negative cheaper),
 * evaluated ("{1} less for each artifact you control"). The spell's own
 * static abilities count (affinity, improvise).
 */
function costDeltas(game: Game, spell: GameObject, _playerId: PlayerId): number[] {
  const out: number[] = [];
  for (const obj of [...permanents(game), spell]) {
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      for (const effect of list(ability.effects)) {
        if (num(effect.kind) !== MODIFY_COST || !matchesBeingCast(game, spell, effect, obj)) continue;
        const amount = effect.amount == null ? 0 : matchHooks().evaluate(game, effect.amount, { source: obj.id, controller: obj.controller });
        if (amount) out.push(amount);
      }
    }
  }
  return out;
}

/** Generic mana added by "spells cost {N} more". */
export const costIncreases = (game: Game, spell: GameObject, playerId: PlayerId): number[] =>
  costDeltas(game, spell, playerId).filter((a) => a > 0);

export const costReductions = (game: Game, spell: GameObject, playerId: PlayerId): number[] =>
  costDeltas(game, spell, playerId).filter((a) => a < 0).map((a) => -a);

