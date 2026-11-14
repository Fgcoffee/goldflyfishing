/**
 * Choosing and checking targets (CR 601.2c, 115). Ported from the targeting
 * part of mtgfish/rules/cr600_spells_and_abilities/cr601_casting.py
 * (targeted_nodes through _has_protection_from).
 *
 * Order is the contract: targets are one group per targeting node, in the
 * order the resolver reaches them (`targetedNodes`).
 */

import { PlayerScopeValues } from "../../ir";
import type { Ability, Effect, PlayerFilter } from "../../ir";
import { EventKind, event } from "../../kernel/events";
import { agentFor, livingPlayers, player, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { isPlayerTarget, NO_PLAYER, playerTarget, targetPlayer, type PlayerId } from "../../kernel/ids";
import type { Agent } from "../cr100_game_concepts/priority";
import { playerShieldedFrom, protectedFrom } from "../cr100_game_concepts/protection";
import { abilityKeyword, hasKeyword, hasSubtype, keywordAbilities, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { emit } from "../emit";
import { flag, ir, list, num } from "../ir_read";
import { CastError } from "./cast_error";
import { constantOf, includesPlayers, isTargeted, playerFilter, targetOptional, targetsAPlayer, targetsItsPlayer } from "./effect_props";
import { characteristicsOf } from "./layers";

const CHOOSE_MODE: number = ir("EffectKind.CHOOSE_MODE");
const SPELL: number = ir("AbilityKind.SPELL");
const OPPONENT_SCOPES = new Set([PlayerScopeValues.OPPONENT, PlayerScopeValues.EACH_OPPONENT, PlayerScopeValues.TARGET_OPPONENT]);

export type Targets = readonly (readonly number[])[];
/** (fewest, most); most null is no limit. */
export type Bounds = readonly [number, number | null];

/**
 * Targeted effect nodes in the order the resolver reaches them. CR 700.2c:
 * with modes chosen, only theirs; `null` (nothing chosen yet) is every mode.
 * A `same_target` node holds no slot of its own (CR 115.3).
 */
export function targetedNodes(effects: readonly Effect[], chosenModes: readonly number[] | null = null): Effect[] {
  const out: Effect[] = [];
  const visit = (node: Effect): void => {
    const children = list(node.children);
    if (num(node.kind) === CHOOSE_MODE && children.length > 0 && chosenModes !== null) {
      for (const index of chosenModes) {
        if (index >= 0 && index < children.length) visit(children[index]!);
      }
      return;
    }
    if (isTargeted(node) && !flag(node.same_target)) out.push(node);
    for (const child of [...children, ...list(node.otherwise)]) visit(child);
  };
  for (const effect of effects) visit(effect);
  return out;
}

/** The effects of a spell's spell abilities (CR 113.6a). */
export function spellEffects(game: Game, spell: GameObject): Effect[] {
  return (characteristicsOf(game, spell).abilities as readonly Ability[])
    .filter((a) => num(a.kind) === SPELL)
    .flatMap((a) => [...list(a.effects)]);
}

const chosenOrNull = (modes: readonly number[]): readonly number[] | null => (modes.length > 0 ? modes : null);

/** A spell's targeted nodes in announcement order; an Aura's enchant target first (CR 303.4a). */
export function targetingEffects(game: Game, spell: GameObject): Effect[] {
  const nodes = targetedNodes(spellEffects(game, spell), chosenOrNull(spell.chosenModes));
  const enchant = auraTargetEffect(characteristicsOf(game, spell));
  if (enchant !== null) nodes.unshift(enchant);
  return nodes;
}

/** The synthetic targeting effect an Aura spell gets from Enchant (CR 303.4a-b); null if none was read. */
export function auraTargetEffect(chars: Characteristics): Effect | null {
  if (!hasSubtype(chars, "Aura")) return null;
  for (const ability of chars.abilities as readonly Ability[]) {
    if (abilityKeyword(ability).toLowerCase() !== "enchant" || ability.quality == null) continue;
    const quality = ability.quality as { $?: string };
    const nothing = ir("EffectKind.NOTHING");
    if (quality.$ === "PlayerFilter") {
      return {
        $: "Effect", kind: nothing, is_targeted: true, text: "enchant",
        players: playerFilter(enchantableScope(quality as PlayerFilter)),
      };
    }
    return { $: "Effect", kind: nothing, targets: ability.quality as never, is_targeted: true, text: "enchant" };
  }
  return null;
}

/** CR 702.5a: the players an "enchant [player]" Aura may enchant, as a target scope. */
export function enchantableScope(quality: PlayerFilter): number {
  return OPPONENT_SCOPES.has(num(quality.scope)) ? PlayerScopeValues.TARGET_OPPONENT! : PlayerScopeValues.TARGET_PLAYER!;
}

/** Everything one targeting node could legally point at: objects, then players (CR 115.4). */
export function candidatesFor(game: Game, source: GameObject, effect: Effect, playerId: PlayerId): number[] {
  if (targetsAPlayer(effect)) {
    return targetablePlayers(game, effect.players ?? null, playerId)
      .filter((pid) => !playerShieldedFrom(game, pid, source, playerId))
      .map(playerTarget);
  }
  const out = matchHooks()
    .find(game, effect.targets!, { source: source.id, controller: playerId })
    .filter((c) => !hasProtectionFrom(game, c, source))
    .map((c) => c.id);
  if (includesPlayers(effect.targets)) {
    for (const p of livingPlayers(game)) {
      // CR 702.16b, 702.11b, 702.18a.
      if (!playerShieldedFrom(game, p.id, source, playerId)) out.push(playerTarget(p.id));
    }
  }
  return out;
}

/** Players a player-only target may point at (CR 115.4); "target opponent" narrows it. */
export function targetablePlayers(game: Game, spec: PlayerFilter | null, playerId: PlayerId): PlayerId[] {
  const living = livingPlayers(game).map((p) => p.id);
  if (spec != null && OPPONENT_SCOPES.has(num(spec.scope))) return living.filter((p) => p !== playerId);
  return living;
}

/** The targeted nodes of one activated or triggered ability. */
export function abilityTargetingEffects(ability: Ability, chosenModes: readonly number[] | null = null): Effect[] {
  return targetedNodes(list(ability.effects), chosenModes);
}

const required = (effect: Effect): boolean => !targetOptional(effect);

/** Targets for a list of targeting nodes, one group each (CR 601.2c), via the agent. */
export function chooseTargets(game: Game, source: GameObject, effects: readonly Effect[], playerId: PlayerId): number[][] {
  if (effects.length === 0) return [];
  const candidates = effects.map((e) => candidatesFor(game, source, e, playerId));
  const agent = agentFor(game, playerId) as Partial<Agent> | null;
  const picked: Targets =
    agent?.chooseTargets === undefined
      ? candidates.map((group) => (group.length > 0 ? [group[0]!] : []))
      : agent.chooseTargets(game, playerId, source.id, candidates);
  const bounds = effects.map((e) => targetBounds(game, e, playerId, source.xValue));
  return settleTargetChoices(effects, candidates, picked, { fillRequired: true, bounds });
}

/** One target for each required slot, obeying "another target", or null (CR 601.2c). */
export function distinctAssignment(effects: readonly Effect[], candidates: Targets): (number | null)[] | null {
  const slots: [number, Effect][] = [];
  effects.forEach((effect, index) => {
    if (required(effect) && index < candidates.length && candidates[index]!.length > 0) slots.push([index, effect]);
  });
  const chosen = new Map<number, number>();
  const place = (position: number): boolean => {
    if (position === slots.length) return true;
    const [index, effect] = slots[position]!;
    const earlier = new Set([...chosen].filter(([i]) => i < index).map(([, c]) => c));
    for (const candidate of candidates[index]!) {
      if (flag(effect.distinct_from_earlier_targets) && earlier.has(candidate)) continue;
      let collides = false;
      for (const [j, c] of chosen) {
        if (j > index && flag(effects[j]!.distinct_from_earlier_targets) && c === candidate) collides = true;
      }
      if (collides) continue;
      chosen.set(index, candidate);
      if (place(position + 1)) return true;
      chosen.delete(index);
    }
    return false;
  };
  if (!place(0)) return null;
  return effects.map((_, index) => chosen.get(index) ?? null);
}

/** Whether every "another target" slot can be told apart at once. */
export function distinctTargetsAvailable(effects: readonly Effect[], candidates: Targets): boolean {
  if (!effects.some((e) => flag(e.distinct_from_earlier_targets))) return true;
  return distinctAssignment(effects, candidates) !== null;
}

/** How many targets one node takes (CR 601.2c, 115.1d). */
export function targetBounds(game: Game, effect: Effect, controller: PlayerId = NO_PLAYER, xValue = 0): Bounds {
  const spec = effect.targets;
  if (spec == null || targetsItsPlayer(effect)) return [1, 1];
  if (spec.count == null) return flag(spec.up_to) ? [0, null] : [1, 1];
  const count = spec.count;
  const most = Math.max(
    0,
    typeof count === "number" ? count : (constantOf(count) ?? matchHooks().evaluate(game, count, { controller, xValue })),
  );
  return [flag(spec.up_to) ? 0 : most, most];
}

/**
 * What an agent chose, cut down to what the rules allow: nothing not offered,
 * nothing twice (CR 115.3), nothing earlier in an "another" slot; with
 * `fillRequired`, empty required slots filled deterministically (CR 601.2c).
 */
export function settleTargetChoices(
  effects: readonly Effect[],
  candidates: Targets,
  picked: Targets,
  options: { fillRequired: boolean; bounds?: readonly Bounds[] },
): number[][] {
  const { fillRequired, bounds } = options;
  let cleaned: number[][] = [];
  const earlier = new Set<number>();
  effects.forEach((effect, index) => {
    const legal = new Set(index < candidates.length ? candidates[index] : []);
    const offered = index < picked.length ? picked[index]! : [];
    const distinct = flag(effect.distinct_from_earlier_targets);
    let kept = [...new Set(offered.filter((id) => legal.has(id) && !(distinct && earlier.has(id))))];
    const [fewest, most] = bounds !== undefined && index < bounds.length ? bounds[index]! : [0, null];
    if (most !== null) kept = kept.slice(0, most);
    const wanted = Math.max(fewest, fillRequired && required(effect) ? 1 : 0);
    if (kept.length < wanted && legal.size > 0) {
      const free = candidates[index]!.filter((id) => !kept.includes(id) && !(distinct && earlier.has(id)));
      kept = [...kept, ...free.slice(0, wanted - kept.length)];
    }
    cleaned.push(kept);
    for (const id of kept) earlier.add(id);
  });
  const boxedIn = effects.some(
    (effect, index) => cleaned[index]!.length === 0 && required(effect) && index < candidates.length && candidates[index]!.length > 0,
  );
  if (fillRequired && boxedIn) {
    const assignment = distinctAssignment(effects, candidates);
    if (assignment !== null) cleaned = cleaned.map((kept, index) => (assignment[index] != null ? [assignment[index]!] : kept));
  }
  return cleaned;
}

/** CR 115.3, 601.2c: no object twice for one "target"; no earlier target in an "another" slot. */
export function checkDistinctTargets(effects: readonly Effect[], targets: Targets): void {
  const earlier = new Set<number>();
  effects.forEach((effect, index) => {
    const chosen = index < targets.length ? targets[index]! : [];
    if (new Set(chosen).size !== chosen.length) throw new CastError("the same target was chosen twice for one target");
    if (flag(effect.distinct_from_earlier_targets) && chosen.some((id) => earlier.has(id))) {
      throw new CastError("another target must differ from the earlier targets");
    }
    for (const id of chosen) earlier.add(id);
  });
}

/** CR 601.2c: a spell can't be cast without legal targets for every target it requires. */
export function validateTargets(game: Game, spell: GameObject, targets: Targets): void {
  const effects = targetedNodes(spellEffects(game, spell), chosenOrNull(spell.chosenModes));
  if (targets.length < effects.length) throw new CastError("not every required target was chosen");
  checkDistinctTargets(effects, targets);
  effects.forEach((effect, index) => {
    const chosen = index < targets.length ? targets[index]! : [];
    if (chosen.length === 0 && !targetOptional(effect)) throw new CastError("a required target was not chosen");
    const [fewest, most] = targetBounds(game, effect, spell.controller, spell.xValue);
    if (chosen.length < fewest) throw new CastError(`${fewest} targets are required, ${chosen.length} chosen`);
    if (most !== null && chosen.length > most) throw new CastError(`at most ${most} targets may be chosen`);
    for (const id of chosen) {
      if (isPlayerTarget(id)) {
        const pid = targetPlayer(id);
        if (player(game, pid).hasLost) throw new CastError("that player has left the game");
        if (playerShieldedFrom(game, pid, spell, spell.controller)) throw new CastError("that player can't be the target of this");
        checkPlayerTarget(game, effect, id, spell.controller);
        continue;
      }
      if (targetsAPlayer(effect)) throw new CastError("only a player can be this target");
      const candidate = game.objects.get(id);
      if (candidate === undefined) throw new CastError("target no longer exists");
      if (effect.targets != null && !matchHooks().matches(game, candidate, effect.targets, { source: spell.id, controller: spell.controller })) {
        throw new CastError(`object ${id} is not a legal target`);
      }
      if (hasProtectionFrom(game, candidate, spell)) throw new CastError(`object ${id} can't be targeted by this spell`);
    }
  });
}

/** CR 115.1, 601.2c: "target opponent" is not you. */
export function checkPlayerTarget(game: Game, effect: Effect, target: number, controller: PlayerId): void {
  if (targetsAPlayer(effect) && !targetablePlayers(game, effect.players ?? null, controller).includes(targetPlayer(target))) {
    throw new CastError("that player is not a legal target");
  }
}

/** A TARGETED event for each chosen object target (CR 115.1), for ward and the like. */
export function announceTargets(game: Game, spell: GameObject, controller: PlayerId): void {
  for (const group of spell.targets) {
    for (const id of group) {
      const target = game.objects.get(id);
      if (target === undefined) continue;
      emit(game, event(EventKind.TARGETED, { objectId: id, player: target.controller, source: spell.id, sourceController: controller }));
    }
  }
}

/** Shroud, hexproof, "hexproof from", protection, as they apply to targeting (CR 702.11, 702.16, 702.18). */
export function hasProtectionFrom(game: Game, target: GameObject, source: GameObject): boolean {
  const chars = characteristicsOf(game, target);
  if (hasKeyword(chars, "Shroud")) return true;
  if (hasKeyword(chars, "Hexproof") && target.controller !== source.controller) return true;
  for (const ability of keywordAbilities(chars, "Hexproof from")) {
    if (target.controller === source.controller) continue;
    const quality = ability.quality as { $?: string } | null | undefined;
    if (quality == null) return true;
    if (quality.$ === "ObjectFilter" && matchHooks().matches(game, source, ability.quality as never, {})) return true;
  }
  return protectedFrom(game, target, source);
}

/** Whether any of an ability's effects is targeted (Python `Ability.is_targeted`). */
export function abilityIsTargeted(ability: Ability): boolean {
  return list(ability.effects).some(containsTargeted);
}
function containsTargeted(effect: Effect): boolean {
  if (isTargeted(effect)) return true;
  return [...list(effect.children), ...list(effect.otherwise)].some(containsTargeted);
}

/** CR 602.2b, 601.2c: an activated ability's chosen targets are legal. */
export function validateAbilityTargets(
  game: Game,
  source: GameObject,
  ability: Ability,
  targets: Targets,
  chosenModes: readonly number[] = [],
): void {
  const effects = targetedNodes(list(ability.effects), chosenOrNull(chosenModes));
  if (targets.length < effects.length) throw new CastError("not every required target was chosen");
  checkDistinctTargets(effects, targets);
  effects.forEach((effect, index) => {
    for (const id of index < targets.length ? targets[index]! : []) {
      if (isPlayerTarget(id)) {
        if (player(game, targetPlayer(id)).hasLost) throw new CastError("that player has left the game");
        checkPlayerTarget(game, effect, id, source.controller);
        continue;
      }
      if (targetsAPlayer(effect)) throw new CastError("only a player can be this target");
      const candidate = game.objects.get(id);
      if (candidate === undefined) throw new CastError("target no longer exists");
      if (effect.targets != null && !matchHooks().matches(game, candidate, effect.targets, { source: source.id, controller: source.controller })) {
        throw new CastError("illegal target");
      }
    }
  });
}
