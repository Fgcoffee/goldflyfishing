/**
 * Cards that beat the rules (CR 101.1). Ported from
 * mtgfish/rules/cr100_game_concepts/cr101_rule_overrides.py.
 *
 * The seam for rules the engine applies to the game itself rather than to an
 * object: a SUSPEND_RULE effect names a rule by its CR number, and each
 * rule's call site asks `ruleIsSuspended` before enforcing it. The list is
 * closed: a rule with no call site is a promise the engine doesn't keep.
 */

import type { Ability, Condition, ObjectFilter, PlayerFilter } from "../../ir";
import { Zone } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { flag, ir, list, num, numSet } from "../ir_read";

/** A rule a card may switch off, by its CR number; each names its call site. */
export const Rule = {
  /** CR 302.6: combat's can-attack check (as in Python; the {T} checks don't ask). */
  SUMMONING_SICKNESS: "302.6",
  /** CR 704.5j: the SBAs' legend rule. */
  LEGEND_RULE: "704.5j",
  /** CR 305.2: legal actions' land plays. */
  ONE_LAND_PER_TURN: "305.2",
  /** CR 508.1f: declaring attackers. */
  ATTACKING_TAPS: "508.1f",
} as const;
export type Rule = (typeof Rule)[keyof typeof Rule];
const RULES: ReadonlySet<string> = new Set(Object.values(Rule));

/** One rule switched off, and for whom; shaped like a Restriction. */
export interface RuleOverride {
  readonly rule: Rule;
  readonly subject: ObjectFilter | null;
  readonly players: PlayerFilter | null;
  readonly source: ObjectId;
  readonly controller: PlayerId;
  readonly condition: Condition | null;
  readonly text: string;
  readonly duration: number;
  readonly createdTurn: number;
}

export const DESCRIPTIONS: Readonly<Record<Rule, string>> = {
  [Rule.SUMMONING_SICKNESS]: "creatures can attack and use {T} abilities the turn they arrive (CR 302.6)",
  [Rule.LEGEND_RULE]: "a player may control two or more legendary permanents with the same name (CR 704.5j)",
  [Rule.ONE_LAND_PER_TURN]: "lands may be played without the one-per-turn limit (CR 305.2)",
  [Rule.ATTACKING_TAPS]: "attacking does not cause a creature to tap (CR 508.1f)",
};

export const overrideText = (o: RuleOverride): string => o.text || `CR ${o.rule} does not apply`;

const STATIC: number = ir("AbilityKind.STATIC");
const SUSPEND_RULE: number = ir("EffectKind.SUSPEND_RULE");

/** Whether `rule` is switched off now for this object or player, and by what (game.rule_is_suspended). */
export function ruleIsSuspended(
  game: Game,
  rule: Rule,
  context: { obj?: GameObject | null; player?: PlayerId } = {},
): RuleOverride | null {
  const obj = context.obj ?? null;
  const player = context.player ?? NO_PLAYER;
  const hooks = matchHooks();
  for (const o of activeOverrides(game)) {
    if (o.rule !== rule) continue;
    const scope = { source: o.source, controller: o.controller };
    if (o.subject !== null && (obj === null || !hooks.matches(game, obj, o.subject, scope))) continue;
    if (o.players !== null) {
      let who = player;
      if (who === NO_PLAYER && obj !== null) who = obj.controller;
      if (who === NO_PLAYER) continue;
      if (!hooks.resolvePlayers(game, o.players, { controller: o.controller }).includes(who)) continue;
    }
    if (o.condition !== null && num(o.condition.kind) !== 0 && !hooks.holds(game, o.condition, scope)) continue;
    return o;
  }
  return null;
}

/** Every suspension in force: the standing ones plus live static abilities (CR 611.3). */
export function activeOverrides(game: Game): RuleOverride[] {
  const out = [...(game.ruleOverrides as RuleOverride[])];
  for (const id of [...game.battlefield, ...game.stack]) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      if (!numSet(ability.functions_in, [Zone.BATTLEFIELD]).has(obj.zone)) continue;
      for (const effect of list(ability.effects)) {
        if (num(effect.kind) !== SUSPEND_RULE || !effect.rule || !RULES.has(effect.rule)) continue;
        out.push({
          rule: effect.rule as Rule,
          subject: effect.targets ?? null,
          players: effect.players ?? null,
          source: obj.id,
          controller: obj.controller,
          condition: ability.static_condition ?? null,
          text: effect.text ?? "",
          duration: 0,
          createdTurn: 0,
        });
      }
    }
  }
  return out;
}

/** Add a suspension from a resolved spell with a duration (not from a static ability). */
export function registerOverride(
  game: Game,
  override: Omit<RuleOverride, "createdTurn" | "source" | "controller" | "condition" | "duration"> &
    Partial<Pick<RuleOverride, "createdTurn" | "source" | "controller" | "condition" | "duration">>,
): RuleOverride {
  const entry: RuleOverride = {
    source: NO_OBJECT,
    controller: NO_PLAYER,
    condition: null,
    duration: 0,
    ...override,
    createdTurn: override.createdTurn || game.turn,
  };
  game.ruleOverrides.push(entry);
  return entry;
}
