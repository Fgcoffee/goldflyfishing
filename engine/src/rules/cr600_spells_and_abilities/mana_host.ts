/**
 * The game side of mana planning: `ManaPlanHost<Game>` for the parser's
 * rules/mana/plan.ts. Ported from mtgfish/rules/cr600_spells_and_abilities/
 * mana_plan.py (mana_options, _still_activatable, _outcomes, _produced,
 * _predict, plan_payment's early return, execute_plan, can_produce).
 */

import type { Ability, Effect } from "../../ir";
import type { CardDef } from "../../kernel/card";
import { Color, Zone } from "../../kernel/enums";
import { permanents, player, type Game } from "../../kernel/game";
import { isLive, type GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { requiresTapping } from "../cr100_game_concepts/costs";
import { isUsableManaAbility } from "../cr200_parts_of_a_card/characteristics";
import { flag, ir, irEqual, list, num, strs } from "../ir_read";
import { isEmpty, type ManaCost } from "../mana/cost";
import { findPayment } from "../mana/payment";
import {
  executePlan,
  optionDrawback,
  planPayment,
  PlanNoLongerHolds,
  sortSourceOptions,
  type ManaOption,
  type ManaPlanHost,
} from "../mana/plan";
import { manaKind, type ManaKind, type ManaPool, type PaymentContext } from "../mana/pool";
import { CastError } from "./cast_error";
import { activationManaCost, canPayActivation, payActivation } from "./paying";
import { characteristicsOf } from "./layers";
import { chooseModes, legalModes, modalEffect } from "./modes";
import {
  ALL_SINGLE_COLORS,
  amount2Of,
  countOf,
  firstColor,
  isSnowSource,
  manaColorChoices,
  manaKindOf,
  manaRestrictionOf,
  newResolution,
  resolveManaAbility,
} from "./resolve";
import { resolveManaTriggers } from "./triggers";
import { constantOf } from "./effect_props";

const ADD_MANA: number = ir("EffectKind.ADD_MANA");
const SEQUENCE: number = ir("EffectKind.SEQUENCE");
const CHOOSE_QUALITY: number = ir("EffectKind.CHOOSE_QUALITY");

/**
 * What a payment is for, as a mana restriction sees it (SpendOnlyOn.permits):
 * the printed types of the card's face; none for a token or emblem, null
 * for a bare payability check.
 */
export function paymentContextOf(obj: GameObject | null): PaymentContext | null {
  if (obj === null) return null;
  const card = obj.card as CardDef | null;
  const face = card?.faces[obj.faceIndex];
  if (face === undefined) return {};
  return { types: face.type_line.types, subtypes: face.type_line.subtypes };
}

export const MANA_HOST: ManaPlanHost<Game> = {
  manaOptions,
  stillActivatable,
  activate(game, playerId, option, ability) {
    const obj = game.objects.get(option.source)!;
    payActivation(game, obj, ability);
    const modes = option.modes.length > 0 ? chooseModes(game, obj, list(ability.effects), playerId, { announced: option.modes }) : [];
    resolveManaAbility(
      newResolution(game, obj.id, playerId, { chosenModes: modes, manaColor: option.color }),
      list(ability.effects),
      { tapped: requiresTapping(ability.cost) },
    );
    resolveManaTriggers(game);
  },
};

/** Every untapped source the player could activate now, one list of alternatives each. */
export function manaOptions(game: Game, playerId: PlayerId): ManaOption[][] {
  const perSource: ManaOption[][] = [];
  for (const obj of [...permanents(game, playerId)].sort((a, b) => a.id - b.id)) {
    const options: ManaOption[] = [];
    (characteristicsOf(game, obj).abilities as readonly Ability[]).forEach((ability, index) => {
      if (!isUsableManaAbility(ability) || !canPayActivation(game, obj, ability)) return;
      const cost = activationManaCost(game, obj, ability);
      const manaCost: ManaCost | null = isEmpty(cost) ? null : cost;
      for (const [modes, color, produces] of outcomes(game, obj, ability, playerId)) {
        if (produces.length === 0) continue;
        options.push({ source: obj.id, abilityIndex: index, modes, color, produces, manaCost, drawback: optionDrawback(ability, modes), ability });
      }
    });
    if (options.length > 0) perSource.push(sortSourceOptions(options));
  }
  return perSource;
}

/** The planned ability if its source is still there, still has it (by identity) and can pay (F61). */
export function stillActivatable(game: Game, option: ManaOption): Ability | null {
  const obj = game.objects.get(option.source);
  if (obj === undefined || !isLive(obj) || obj.zone !== Zone.BATTLEFIELD) return null;
  const abilities = characteristicsOf(game, obj).abilities as readonly Ability[];
  if (option.abilityIndex < 0 || option.abilityIndex >= abilities.length) return null;
  let ability: Ability | undefined = abilities[option.abilityIndex];
  if (option.ability !== undefined && !irEqual(ability, option.ability)) {
    ability = abilities.find((a) => irEqual(a, option.ability));
    if (ability === undefined) return null;
  }
  return canPayActivation(game, obj, ability!) ? ability! : null;
}

type Outcome = [number[], Color, ManaKind[]];

/** (modes, colour, mana added) for each way the ability can be activated. */
function outcomes(game: Game, obj: GameObject, ability: Ability, playerId: PlayerId): Outcome[] {
  const effects = list(ability.effects);
  const modal = modalEffect(effects);
  if (modal === null) return produced(game, obj, effects, playerId).map(([c, p]) => [[], c, p]);
  const out: Outcome[] = [];
  for (const mode of legalModes(game, obj, modal, playerId)) {
    const branch = effects.map((e) => (e === modal ? list(modal.children)[mode]! : e));
    for (const [c, p] of produced(game, obj, branch, playerId)) out.push([[mode], c, p]);
  }
  return out;
}

function addManaEffects(effects: readonly Effect[]): Effect[] {
  const out: Effect[] = [];
  for (const e of effects) {
    if (num(e.kind) === ADD_MANA) out.push(e);
    else if (num(e.kind) === SEQUENCE) out.push(...addManaEffects(list(e.children)));
  }
  return out;
}

function choosesColour(effects: readonly Effect[]): boolean {
  return effects.some(
    (e) => (num(e.kind) === CHOOSE_QUALITY && strs(e.keywords).includes("color")) || (num(e.kind) === SEQUENCE && choosesColour(list(e.children))),
  );
}

/** (colour choice, mana) pairs for effects with no modes left; one "any colour" choice is branched on. */
function produced(game: Game, obj: GameObject, effects: readonly Effect[], playerId: PlayerId): [Color, ManaKind[]][] {
  const adds = addManaEffects(effects);
  if (adds.length === 0) return [];
  if (choosesColour(effects) && adds.some((e) => flag(e.colors_chosen))) {
    // "Choose a color. Add ... of that color": each choice predicted with it recorded.
    const before = obj.chosenColor;
    try {
      return ALL_SINGLE_COLORS.map((color): [Color, ManaKind[]] => {
        obj.chosenColor = color;
        return [color, adds.flatMap((e) => predict(game, obj, e, playerId, color))];
      });
    } finally {
      obj.chosenColor = before;
    }
  }
  let menu = 0;
  for (const e of adds) {
    if (list(e.mana_produced).length === 0 && !flag(e.colors_chosen) && num(e.colors)) menu |= manaColorChoices(game, playerId, e);
  }
  const choices = menu ? ALL_SINGLE_COLORS.filter((c) => (menu & c) !== 0) : [Color.NONE];
  return choices.map((color): [Color, ManaKind[]] => [color, adds.flatMap((e) => predict(game, obj, e, playerId, color))]);
}

/** What ADD_MANA would add, without adding it. */
function predict(game: Game, obj: GameObject, effect: Effect, playerId: PlayerId, color: Color): ManaKind[] {
  const snow = isSnowSource(game, obj);
  const resolution = newResolution(game, obj.id, playerId);
  const restriction = manaRestrictionOf(effect);
  const symbols = list(effect.mana_produced);
  const times = <T>(n: number, make: () => T): T[] => Array.from({ length: Math.max(0, n) }, make);
  if (symbols.length > 0) {
    const repeat = constantOf(effect.amount2 ?? { $: "Value" }) === 0 ? 1 : Math.max(0, amount2Of(resolution, effect));
    return times(repeat, () => symbols.map((s) => manaKindOf(s, { snow, restriction }))).flat();
  }
  const amount = countOf(resolution, effect);
  if (flag(effect.colors_chosen)) return times(amount, () => manaKind((obj.chosenColor || Color.NONE) as Color, { snow, restriction }));
  if (num(effect.colors)) {
    const menu = manaColorChoices(game, playerId, effect);
    if (!menu) return []; // CR 903.4f
    const pick = color && (menu & color) === color ? color : firstColor(menu);
    return times(amount, () => manaKind(pick, { snow, restriction }));
  }
  return times(amount, () => manaKind(Color.NONE, { snow, restriction }));
}

/**
 * The mana abilities to activate so `cost` can be paid, or null; [] when the
 * pool already covers it (Python plan_payment). One life is kept back.
 */
export function planPaymentFor(
  game: Game,
  playerId: PlayerId,
  cost: ManaCost,
  context: GameObject | null = null,
  options: { pool?: ManaPool; sources?: ManaOption[][] } = {},
): ManaOption[] | null {
  const p = player(game, playerId);
  const pool = options.pool ?? p.manaPool;
  const paymentContext = paymentContextOf(context);
  if (findPayment(pool, cost, { lifeAvailable: p.life - 1, context: paymentContext }) !== null) return [];
  const sources = options.sources ?? manaOptions(game, playerId);
  if (sources.length === 0) return null;
  return planPayment({ pool, life: p.life, cost, sources, context: paymentContext });
}

/** Whether the pool plus untapped sources can pay `cost`. */
export function canProduceFor(game: Game, playerId: PlayerId, cost: ManaCost, context: GameObject | null = null): boolean {
  return planPaymentFor(game, playerId, cost, context) !== null;
}

/** Activate the planned abilities in order; a plan that no longer holds rewinds the cast (F61). */
export function executePlanFor(game: Game, playerId: PlayerId, plan: readonly ManaOption[]): void {
  try {
    executePlan(MANA_HOST, game, playerId, plan);
  } catch (e) {
    if (e instanceof PlanNoLongerHolds) throw new CastError(e.message);
    throw e;
  }
}
