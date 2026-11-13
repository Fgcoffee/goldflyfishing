/**
 * Derived properties of an IR Effect, ported from the properties of
 * `Effect` in mtgfish/rules/cr600_spells_and_abilities/effects.py
 * (walk, targets_its_player, targets_a_player, target_optional).
 */

import { PlayerScopeValues } from "../../ir";
import type { Effect, ObjectFilter, PlayerFilter, Value } from "../../ir";
import { flag, list, num } from "../ir_read";

/** This effect and every descendant, children before `otherwise` (pre-order). */
export function* walk(effect: Effect): Generator<Effect> {
  yield effect;
  for (const child of list(effect.children)) yield* walk(child);
  for (const child of list(effect.otherwise)) yield* walk(child);
}

export const isTargeted = (e: Effect): boolean => flag(e.is_targeted);

const TARGET_PLAYER_SCOPES = new Set([PlayerScopeValues.TARGET_PLAYER, PlayerScopeValues.TARGET_OPPONENT]);

/** "Target player sacrifices a creature": the target is the player (CR 115.1). */
export function targetsItsPlayer(e: Effect): boolean {
  return isTargeted(e) && e.targets != null && e.players != null && TARGET_PLAYER_SCOPES.has(num(e.players.scope));
}

/** Whether this instruction's target is a player and only a player. */
export function targetsAPlayer(e: Effect): boolean {
  return isTargeted(e) && (e.targets == null || targetsItsPlayer(e));
}

/** "Up to one target": the target may be left unchosen (CR 601.2c). */
export function targetOptional(e: Effect): boolean {
  return e.targets != null && flag(e.targets.up_to) && !targetsItsPlayer(e);
}

export const includesPlayers = (spec: ObjectFilter | null | undefined): boolean => spec != null && flag(spec.includes_players);

/** A Value that is a plain constant, and its number; null otherwise. */
export function constantOf(value: Value | number | null | undefined): number | null {
  if (value == null) return null;
  if (typeof value === "number") return value;
  return num(value.kind) === 0 ? num(value.constant) : null;
}

export const playerFilter = (scope: number): PlayerFilter => ({ $: "PlayerFilter", scope: scope as never });
