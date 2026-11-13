/**
 * Prohibitions and permissions (CR 101.2, 101.3, 113.6, 601.3). Ported from
 * mtgfish/rules/cr500_turn_structure/restrictions.py.
 *
 * CR 101.2: when one effect allows something and another says it can't
 * happen, the "can't" wins. Every "may I?" question goes through
 * `prohibited`, and a new prohibition is data, not code. `permitted` is the
 * mirror image: "you may cast sorceries as though they had flash".
 *
 * PORT: the blocking requirements half of the Python module (Requirement,
 * BlockObligation, the CR 509.1c search) comes with combat.
 */

import { ActValues, PlayerScopeValues } from "../../ir";
import type { Ability, Effect, ObjectFilter, Restriction } from "../../ir";
import { Zone } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type PlayerId } from "../../kernel/ids";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { flag, ir, list, num, numSet } from "../ir_read";

const STATIC: number = ir("AbilityKind.STATIC");
const RESTRICTION: number = ir("EffectKind.RESTRICTION");
const PERMISSION: number = ir("EffectKind.PERMISSION");

/** Things a rule or effect can forbid or allow: the IR's Act enum. */
export const Act = ActValues as Readonly<Record<
  | "CAST_SPELL" | "PLAY_LAND" | "CAST_AS_THOUGH_FLASH" | "ACTIVATE_ABILITY" | "ACTIVATE_MANA_ABILITY"
  | "ACTIVATE_LOYALTY_ABILITY" | "ATTACK" | "ATTACK_PLAYER" | "ATTACK_PLANESWALKER" | "BLOCK" | "BE_BLOCKED"
  | "BE_COUNTERED" | "BE_TARGETED" | "COPY" | "UNTAP" | "TAP" | "BE_DESTROYED" | "BE_SACRIFICED"
  | "BE_REGENERATED" | "ENTER_BATTLEFIELD" | "LEAVE_BATTLEFIELD" | "HAVE_COUNTERS_PLACED" | "BE_EXILED"
  | "CHANGE_CONTROL" | "PHASE_OUT" | "DRAW_CARD" | "GAIN_LIFE" | "LOSE_LIFE" | "SEARCH_LIBRARY" | "WIN_GAME"
  | "LOSE_GAME" | "BE_DEALT_DAMAGE" | "SHUFFLE" | "CAST_MORE_THAN_ONE_SPELL" | "UNTAP_DURING_UNTAP_STEP"
  | "LIFE_TOTAL_CHANGE" | "LOOK_AT_TOP_CARD" | "DISCARD_TO_HAND_SIZE",
  number
>>;
export type Act = number;

export interface ActContext {
  readonly obj?: GameObject | null;
  readonly player?: PlayerId;
  readonly counterpart?: GameObject | null;
}

/**
 * Acts a player performs. A restriction on one of these that names players
 * ("your opponents can't cast spells") is about those players, not its source.
 */
export const PLAYER_ACTS: ReadonlySet<number> = new Set([
  Act.CAST_SPELL, Act.PLAY_LAND, Act.ACTIVATE_ABILITY, Act.ACTIVATE_MANA_ABILITY,
  Act.ACTIVATE_LOYALTY_ABILITY, Act.DRAW_CARD, Act.GAIN_LIFE, Act.LOSE_LIFE, Act.SEARCH_LIBRARY,
  Act.WIN_GAME, Act.LOSE_GAME, Act.SHUFFLE, Act.LIFE_TOTAL_CHANGE,
]);

/** A restriction's text, for logs: "can't cast spell" when it has none. */
export function restrictionText(r: Restriction): string {
  const name = Object.entries(ActValues).find(([, v]) => v === num(r.act))?.[0] ?? String(r.act);
  return r.text || `can't ${name.toLowerCase().replaceAll("_", " ")}`;
}

/** Whether `act` is forbidden right now, and by what (CR 101.2). */
export function prohibited(game: Game, act: Act, context: ActContext = {}): Restriction | null {
  for (const restriction of active(game)) {
    if (restrictionApplies(game, restriction, act, context)) return restriction;
  }
  return null;
}

export function allowed(game: Game, act: Act, context: ActContext = {}): boolean {
  return prohibited(game, act, context) === null;
}

const conditionIsAlways = (r: Restriction): boolean => r.condition === undefined || num(r.condition.kind) === 0;

/**
 * Whether one restriction covers `act` by `obj` / `player`. Split out so a
 * restriction lifted by paying a cost (CR 508.1h, 509.1d) is matched by the
 * same rules as an absolute one.
 */
export function restrictionApplies(game: Game, restriction: Restriction, act: Act, context: ActContext = {}): boolean {
  const { obj = null, player = NO_PLAYER, counterpart = null } = context;
  if (num(restriction.act) !== act) return false;
  const hooks = matchHooks();
  const source = num(restriction.source, NO_OBJECT);
  const controller = num(restriction.controller, NO_PLAYER);
  const scope = { source, controller };

  const playerScoped = PLAYER_ACTS.has(act) && restriction.players != null;
  if (restriction.subject != null) {
    if (obj === null) return false;
    if (!hooks.matches(game, obj, asProposed(act, restriction.subject), scope)) return false;
  } else if (source !== NO_OBJECT && obj !== null && !playerScoped) {
    // No subject: the restriction's own source ("this spell can't be
    // countered") - unless it names players ("players can't cast spells").
    if (obj.id !== source) return false;
  }

  if (restriction.players != null) {
    let target = player;
    if (target === NO_PLAYER && obj !== null) target = obj.controller;
    if (target === NO_PLAYER) return false;
    if (!restrictedPlayers(game, restriction).includes(target)) return false;
  }

  if (restriction.counterpart != null) {
    if (counterpart === null) return false;
    if (!hooks.matches(game, counterpart, restriction.counterpart, scope)) return false;
  }

  if (!conditionIsAlways(restriction) && !hooks.holds(game, restriction.condition!, scope)) return false;
  return true;
}

/**
 * The subject of a casting or land-play prohibition, asked of the card before
 * it moves: the stack or battlefield a "spells"/"lands" filter defaults to is
 * where the card is going, not a constraint on it (CR 601.2a, 305.1).
 */
function asProposed(act: Act, subject: ObjectFilter): ObjectFilter {
  const destination = act === Act.CAST_SPELL ? Zone.STACK : act === Act.PLAY_LAND ? Zone.BATTLEFIELD : null;
  if (destination === null) return subject;
  const zones = numSet(subject.zones as { $set: number[] } | undefined, [Zone.BATTLEFIELD]);
  if (!zones.has(destination)) return subject;
  zones.delete(destination);
  return { ...subject, zones: { $set: [...zones].sort((a, b) => a - b) } } as ObjectFilter;
}

/** Who a restriction's player filter names; "defending player" from its source (CR 506.2). */
function restrictedPlayers(game: Game, restriction: Restriction): PlayerId[] {
  const hooks = matchHooks();
  const players = restriction.players!;
  const source = num(restriction.source, NO_OBJECT);
  if (num(players.scope) === PlayerScopeValues.DEFENDING_PLAYER) return hooks.defendingPlayers(game, source);
  return hooks.resolvePlayers(game, players, { controller: num(restriction.controller, NO_PLAYER) });
}

/**
 * Restrictions or permissions in force from live static abilities on the
 * battlefield and the stack (CR 604.3: split second works on the stack),
 * plus the standing ones. Rebuilt, not persisted (CR 611.3).
 */
function collect(game: Game, kind: number, standing: readonly unknown[]): Restriction[] {
  const out = [...(standing as Restriction[])];
  for (const id of [...game.battlefield, ...game.stack]) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      if (!numSet(ability.functions_in, [Zone.BATTLEFIELD]).has(obj.zone)) continue;
      for (const effect of list(ability.effects) as readonly Effect[]) {
        if (num(effect.kind) !== kind) continue;
        for (const r of list(effect.restrictions)) {
          out.push({
            $: "Restriction",
            act: r.act,
            subject: r.subject ?? effect.targets ?? null,
            players: r.players ?? effect.players ?? null,
            source: obj.id,
            controller: obj.controller,
            condition: ability.static_condition ?? { $: "Condition" },
            counterpart: r.counterpart ?? null,
            text: r.text || effect.text || "",
          } as Restriction);
        }
      }
    }
  }
  return out;
}

function active(game: Game): Restriction[] {
  if (game.restrictionsCache !== null && game.restrictionsEpoch === game.epoch) {
    return game.restrictionsCache as Restriction[];
  }
  const out = collect(game, RESTRICTION, game.standingRestrictions);
  game.restrictionsCache = out;
  game.restrictionsEpoch = game.epoch;
  return out;
}

function activePermissions(game: Game): Restriction[] {
  if (game.permissionsCache !== null && game.permissionsEpoch === game.epoch) {
    return game.permissionsCache as Restriction[];
  }
  const out = collect(game, PERMISSION, game.standingPermissions);
  game.permissionsCache = out;
  game.permissionsEpoch = game.epoch;
  return out;
}

/** Whether an effect permits `act` the rules would otherwise deny (CR 113.6), and which. */
export function permitted(game: Game, act: Act, context: ActContext = {}): Restriction | null {
  const { obj = null, player = NO_PLAYER } = context;
  const hooks = matchHooks();
  for (const permission of activePermissions(game)) {
    if (num(permission.act) !== act) continue;
    const scope = { source: num(permission.source, NO_OBJECT), controller: num(permission.controller, NO_PLAYER) };
    if (permission.subject != null) {
      if (obj === null) continue;
      if (!hooks.matches(game, obj, permission.subject, scope)) continue;
    }
    if (permission.players != null && player !== NO_PLAYER) {
      if (!hooks.resolvePlayers(game, permission.players, { controller: scope.controller }).includes(player)) continue;
    }
    // Python asks holds() even of an ALWAYS condition; same answer.
    if (!conditionIsAlways(permission) && !hooks.holds(game, permission.condition!, scope)) continue;
    return permission;
  }
  return null;
}

/** Add a permission from a resolved spell with a duration ("this turn"). */
export function registerStandingPermission(game: Game, permission: Restriction): Restriction {
  const entry = permission.created_turn ? permission : { ...permission, created_turn: game.turn };
  game.standingPermissions.push(entry);
  game.permissionsEpoch = -1;
  return entry;
}

/** Add a prohibition from a resolved spell with a duration (CR 611.2b). */
export function registerStanding(game: Game, restriction: Restriction): Restriction {
  const entry = restriction.created_turn ? restriction : { ...restriction, created_turn: game.turn };
  game.standingRestrictions.push(entry);
  game.restrictionsCache = null;
  return entry;
}
