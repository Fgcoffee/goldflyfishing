/**
 * Modal spells and abilities (CR 700.2). Ported from the mode part of
 * mtgfish/rules/cr600_spells_and_abilities/cr601_casting.py (modal_effect
 * through _cleaned_modes, and ability_targets_available).
 */

import type { Ability, Condition, Effect } from "../../ir";
import { agentFor, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import type { ObjectId, PlayerId } from "../../kernel/ids";
import { flag, ir, list, num } from "../ir_read";
import { constantOf, includesPlayers, isTargeted, targetOptional, targetsAPlayer, walk } from "./effect_props";
import { candidatesFor, distinctTargetsAvailable, targetablePlayers, targetedNodes } from "./targeting";

const CHOOSE_MODE: number = ir("EffectKind.CHOOSE_MODE");

/** What an agent may implement to choose modes; options are [index, mode, weight]. */
export interface ModeChooser {
  chooseModes?(game: Game, player: PlayerId, source: ObjectId, options: [number, Effect, number][], budget: number): readonly number[];
}

/** The modal instruction among these effects, if any (CR 700.2); the first found. */
export function modalEffect(effects: readonly Effect[]): Effect | null {
  for (const effect of effects) {
    for (const node of walk(effect)) {
      if (num(node.kind) === CHOOSE_MODE && list(node.children).length > 0) return node;
    }
  }
  return null;
}

/** Which modes may be chosen (CR 700.2a-b): a mode whose targets can't all be supplied can't be. */
export function legalModes(game: Game, source: GameObject | null, modal: Effect, player: PlayerId): number[] {
  const out: number[] = [];
  list(modal.children).forEach((mode, index) => {
    if (modeIsChoosable(game, source, mode, player)) out.push(index);
  });
  return out;
}

function modeIsChoosable(game: Game, source: GameObject | null, mode: Effect, player: PlayerId): boolean {
  for (const node of walk(mode)) {
    if (!isTargeted(node)) continue;
    // "Up to one" is satisfiable with none; a player is always there (CR 115.4).
    if (targetOptional(node) || includesPlayers(node.targets)) continue;
    if (source !== null) {
      if (candidatesFor(game, source, node, player).length === 0) return false;
      continue;
    }
    // The source is gone (a dies-trigger): the filters alone decide.
    if (targetsAPlayer(node)) {
      if (targetablePlayers(game, node.players ?? null, player).length === 0) return false;
      continue;
    }
    if (matchHooks().find(game, node.targets!, { controller: player }).length === 0) return false;
  }
  return true;
}

/** How much the instruction may spend on modes (CR 700.2, 700.2i); one by default. */
export function modeBudget(modal: Effect): number {
  const c = constantOf(modal.amount);
  return c !== null && c > 0 ? c : 1;
}

/** The fewest modes an up-to choice may take; spree takes at least one (CR 702.172a). */
export function modeFloor(modal: Effect): number {
  return Math.max(num(modal.modes_at_least), list(modal.mode_costs).length > 0 ? 1 : 0);
}

/** What choosing this mode costs against the budget: its pawprints, else one (CR 700.2i). */
export function modeWeight(modal: Effect, index: number): number {
  const weights = list(modal.mode_weights);
  const w = index < weights.length ? Number(weights[index]) : 0;
  return w > 0 ? w : 1;
}

/**
 * Choose the modes as it goes on the stack: a spell (CR 601.2b), an
 * activated ability (602.2b), a triggered one (603.3c). [] means no mode
 * could legally be chosen (CR 700.2b). `announced` is filtered for legality.
 */
export function chooseModes(
  game: Game,
  source: GameObject | null,
  effects: readonly Effect[],
  player: PlayerId,
  options: { announced?: readonly number[]; sourceId?: ObjectId } = {},
): number[] {
  const found = modalEffect(effects);
  if (found === null) return [];
  const identifier = source !== null ? source.id : (options.sourceId ?? 0);
  const modal = upgradedHeader(game, found, identifier, player);
  const available = legalModes(game, source, modal, player);
  if (available.length === 0) return [];
  const budget = modeBudget(modal);
  const announced = options.announced ?? [];
  if (announced.length > 0) return cleanedModes(announced, available, modal, budget);
  const agent = agentFor(game, player) as ModeChooser | null;
  if (agent?.chooseModes === undefined) return cleanedModes([], available, modal, budget);
  const children = list(modal.children);
  const offered = available.map((i): [number, Effect, number] => [i, children[i]!, modeWeight(modal, i)]);
  return cleanedModes(agent.chooseModes(game, player, identifier, offered, budget), available, modal, budget);
}

/** "If you control a commander, you may choose both instead", asked as the modes are chosen. */
function upgradedHeader(game: Game, modal: Effect, sourceId: ObjectId, player: PlayerId): Effect {
  const instead = list(modal.modes_instead);
  if (instead.length === 0) return modal;
  const [condition, budget, upTo, atLeast] = instead as [Condition, number, boolean, number];
  if (!matchHooks().holds(game, condition, { source: sourceId, controller: player })) return modal;
  return {
    ...modal,
    amount: { $: "Value", constant: budget },
    modes_up_to: upTo,
    modes_at_least: atLeast,
    modes_instead: [],
  };
}

/** Whatever was asked for, reduced to a legal choice within the budget (CR 700.2, 700.2d, 700.2i). */
function cleanedModes(picked: readonly number[], available: readonly number[], modal: Effect, budget: number): number[] {
  const legal = new Set(available);
  const repeat = flag(modal.modes_may_repeat);
  const upTo = flag(modal.modes_up_to);
  const kept: number[] = [];
  let spent = 0;
  for (const index of picked) {
    if (!legal.has(index)) continue;
    if (!repeat && kept.includes(index)) continue;
    const cost = modeWeight(modal, index);
    if (spent + cost > budget) continue;
    kept.push(index);
    spent += cost;
  }
  const floor = modeFloor(modal);
  if (upTo && kept.length >= floor) return kept;
  for (const index of available) {
    if (upTo && kept.length >= floor) break;
    if (!repeat && kept.includes(index)) continue;
    const cost = modeWeight(modal, index);
    if (spent + cost > budget) continue;
    kept.push(index);
    spent += cost;
  }
  return kept;
}

/** CR 602.2b / 601.2c: an ability needing a target needs a legal one (one legal mode suffices). */
export function abilityTargetsAvailable(game: Game, source: GameObject, ability: Ability, player: PlayerId): boolean {
  const effects = list(ability.effects);
  const modal = modalEffect(effects);
  if (modal !== null && legalModes(game, source, modal, player).length === 0) return false;
  const nodes = targetedNodes(effects, []);
  for (const node of nodes) {
    if (targetOptional(node) || includesPlayers(node.targets)) continue;
    if (candidatesFor(game, source, node, player).length === 0) return false;
  }
  if (!nodes.some((n) => flag(n.distinct_from_earlier_targets))) return true;
  return distinctTargetsAvailable(nodes, nodes.map((n) => candidatesFor(game, source, n, player)));
}
