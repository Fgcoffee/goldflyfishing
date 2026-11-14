/**
 * Prohibitions and permissions (CR 101.2, 101.3, 113.6, 601.3). Ported from
 * mtgfish/rules/cr500_turn_structure/restrictions.py.
 *
 * CR 101.2: when one effect allows something and another says it can't
 * happen, the "can't" wins. Every "may I?" question goes through
 * `prohibited`, and a new prohibition is data, not code. `permitted` is the
 * mirror image: "you may cast sorceries as though they had flash".
 *
 * The requirements half (Requirement, BlockObligation, the CR 509.1c
 * search) is at the end.
 */

import { ActValues, PlayerScopeValues } from "../../ir";
import type { Ability, Condition, Effect, ObjectFilter, Restriction } from "../../ir";
import { CardType, Zone } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { hasKeyword } from "../cr200_parts_of_a_card/characteristics";
import * as combatModule from "./combat";
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

// -- requirements (CR 508.1e, 509.1c)
//
// A requirement is not a prohibition with the sign flipped. A prohibition is
// answered one act at a time and CR 101.2 makes it absolute; a requirement
// is answered about the declaration as a whole: CR 509.1c makes a block
// legal only if no other legal block obeys more requirements. Hence a
// search, not a loop.

/** The engine's spellings of the blocking requirements (keyword strings, not Acts). */
export const BLOCKS_IF_ABLE = "Blocks each combat if able";
export const MUST_BE_BLOCKED = "Must be blocked if able";
export const BLOCKED_BY_ALL_ABLE = "All creatures able to block it do so";

/** "This creature" - the requirement's own source, for the other participant. */
export const SOURCE_OBJECT: ObjectFilter = { $: "ObjectFilter", source_only: true };

/** Nothing in CR 509.1 caps how many may block one attacker. */
const UNBOUNDED = 1 << 30;

/** Partial declarations the CR 509.1c search looks at before settling for its best. */
const SEARCH_BUDGET = 200_000;

/**
 * One "must" in force (CR 508.1e, 509.1c). `subject` is who is obliged (the
 * blocker for BLOCK, the attacker for BE_BLOCKED; null: the source);
 * `counterpart` the other participant (null: any). One requirement per
 * object matching `subject`: that is how the rules count.
 */
export interface Requirement {
  readonly act: Act;
  readonly subject: ObjectFilter | null;
  readonly counterpart: ObjectFilter | null;
  readonly source: ObjectId;
  readonly controller: PlayerId;
  readonly condition: Condition;
  readonly text: string;
  /** A standing requirement's Duration and the turn it began (CR 611.2b). */
  readonly duration: number;
  readonly createdTurn: number;
}

export function requirement(act: Act, fields: Partial<Omit<Requirement, "act">> = {}): Requirement {
  return {
    act,
    subject: null,
    counterpart: null,
    source: NO_OBJECT,
    controller: NO_PLAYER,
    condition: { $: "Condition" },
    text: "",
    duration: 0,
    createdTurn: 0,
    ...fields,
  };
}

/** Keyword ability -> the requirement it creates. */
const BLOCK_REQUIREMENT_KEYWORDS: ReadonlyMap<string, Requirement> = new Map([
  [BLOCKS_IF_ABLE.toLowerCase(), requirement(Act.BLOCK, { text: "blocks each combat if able" })],
  [MUST_BE_BLOCKED.toLowerCase(), requirement(Act.BE_BLOCKED, { text: "must be blocked if able" })],
  [
    BLOCKED_BY_ALL_ABLE.toLowerCase(),
    requirement(Act.BLOCK, {
      subject: { $: "ObjectFilter", types_all: CardType.CREATURE as never },
      counterpart: SOURCE_OBJECT,
      text: "all creatures able to block it do so",
    }),
  ],
]);

/**
 * One requirement resolved against the creatures in combat: the pairs that
 * would obey it; an empty list means "any".
 */
export interface BlockObligation {
  readonly blockers: readonly ObjectId[];
  readonly attackers: readonly ObjectId[];
  readonly text: string;
}

type Assignment = ReadonlyMap<ObjectId, ObjectId>;

/** Whether a blocker -> attacker declaration obeys this. */
export function obligationSatisfied(o: BlockObligation, assignment: Assignment): boolean {
  for (const [blocker, attacker] of assignment) {
    if (o.blockers.length > 0 && !o.blockers.includes(blocker)) continue;
    if (o.attackers.length > 0 && !o.attackers.includes(attacker)) continue;
    return true;
  }
  return false;
}

/** Whether any of `blockers` could still obey it (the search's only pruning). */
function obligationReachable(o: BlockObligation, blockers: readonly ObjectId[], options: ReadonlyMap<ObjectId, readonly ObjectId[]>): boolean {
  for (const blocker of blockers) {
    if (o.blockers.length > 0 && !o.blockers.includes(blocker)) continue;
    for (const attacker of options.get(blocker) ?? []) {
      if (o.attackers.length === 0 || o.attackers.includes(attacker)) return true;
    }
  }
  return false;
}

/** Every "must" in force, rebuilt from live permanents (CR 611.3), plus standing ones. */
export function requirements(game: Game): Requirement[] {
  const out = (game.standingRequirements as Requirement[]).slice();
  for (const objectId of game.battlefield.slice()) {
    const obj = game.objects.get(objectId);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities) {
      if (!ability.keyword) continue;
      const template = BLOCK_REQUIREMENT_KEYWORDS.get(ability.keyword.trim().toLowerCase());
      if (template === undefined) continue;
      out.push({ ...template, source: obj.id, controller: obj.controller, condition: ability.static_condition ?? { $: "Condition" } });
    }
  }
  return out;
}

/** Which of `pool` a filter names, or null for "unsaid". */
function named(game: Game, pool: readonly GameObject[], spec: ObjectFilter | null, r: Requirement): ObjectId[] | null {
  if (spec === null) return null;
  const hooks = matchHooks();
  return pool.filter((obj) => hooks.matches(game, obj, spec, { source: r.source, controller: r.controller })).map((obj) => obj.id);
}

/** The combat as the requirements half reads it. */
interface CombatAttacks {
  readonly attacking: ReadonlyMap<ObjectId, PlayerId>;
}

function attackersOf(game: Game, combat: CombatAttacks, defender: PlayerId): GameObject[] {
  return [...combat.attacking.keys()]
    .sort((a, b) => a - b)
    .filter((id) => combat.attacking.get(id) === defender && game.objects.has(id))
    .map((id) => game.objects.get(id)!);
}

/**
 * The requirements CR 509.1c counts, for one defending player. One whose
 * counterpart names nothing in this combat is dropped: nothing obeys it.
 */
export function blockObligations(game: Game, combat: CombatAttacks, defender: PlayerId, candidates: readonly GameObject[]): BlockObligation[] {
  const attackers = attackersOf(game, combat, defender);
  if (attackers.length === 0 || candidates.length === 0) return [];
  const hooks = matchHooks();
  const out: BlockObligation[] = [];
  for (const r of requirements(game)) {
    if (r.act !== Act.BLOCK && r.act !== Act.BE_BLOCKED) continue;
    if (!conditionAlways(r.condition) && !hooks.holds(game, r.condition, { source: r.source, controller: r.controller })) continue;
    const [subjectPool, otherPool] = r.act === Act.BLOCK ? [candidates, attackers] : [attackers, candidates];
    let subjects = named(game, subjectPool, r.subject, r);
    if (subjects === null) subjects = subjectPool.filter((obj) => obj.id === r.source).map((obj) => obj.id);
    const others = named(game, otherPool, r.counterpart, r);
    if (others !== null && others.length === 0) continue;
    for (const subjectId of subjects) {
      out.push(
        r.act === Act.BLOCK
          ? { blockers: [subjectId], attackers: others ?? [], text: r.text }
          : { blockers: others ?? [], attackers: [subjectId], text: r.text },
      );
    }
  }
  return out;
}

const conditionAlways = (c: Condition | null | undefined): boolean => c == null || num(c.kind) === ir("ConditionKind.ALWAYS");

/**
 * Apply CR 509.1b-c to a proposed declaration of blockers: obey every
 * restriction and the most requirements any legal declaration could,
 * keeping the defender's own choices where compatible. With no requirements
 * the proposal comes back untouched. A creature that can't block unless a
 * cost is paid is treated as one that does not block.
 */
export function enforceBlockRequirements(
  game: Game,
  combat: CombatAttacks,
  defender: PlayerId,
  candidates: readonly GameObject[],
  proposal: ReadonlyMap<ObjectId, readonly ObjectId[]>,
): Map<ObjectId, readonly ObjectId[]> {
  const blockers = candidates.filter((obj) => obj.controller === defender && combatModule.canBlockAtAll(game, obj));
  const obligations = blockObligations(game, combat, defender, blockers);
  if (obligations.length === 0) return new Map(proposal);

  const attackers = attackersOf(game, combat, defender);
  // CR 509.1b: every pair the declaration may use, restrictions applied.
  const options = new Map<ObjectId, ObjectId[]>();
  for (const blocker of blockers) {
    const legal = attackers.filter((attacker) => combatModule.canBlock(game, blocker, attacker)).map((a) => a.id);
    if (legal.length > 0) options.set(blocker.id, legal);
  }
  const bounds = new Map(attackers.map((attacker) => [attacker.id, blockCountBounds(game, attacker)] as const));
  const preferred = new Map<ObjectId, ObjectId | null>();
  for (const [blockerId, legal] of options) {
    preferred.set(blockerId, (proposal.get(blockerId) ?? []).find((a) => legal.includes(a)) ?? null);
  }
  const assignment = maximiseBlocks(obligations, options, bounds, preferred);
  const result = new Map<ObjectId, readonly ObjectId[]>();
  for (const blockerId of [...assignment.keys()].sort((a, b) => a - b)) {
    const attackerId = assignment.get(blockerId)!;
    const declared = (proposal.get(blockerId) ?? []).slice();
    // A multiple block declared through some effect: left as declared.
    result.set(blockerId, declared.includes(attackerId) && declared.length > 1 ? declared : [attackerId]);
  }
  return result;
}

/** How many may block this attacker at once: menace needs two (CR 702.111b). */
function blockCountBounds(game: Game, attacker: GameObject): [number, number] {
  return [hasKeyword(characteristicsOf(game, attacker), "Menace") ? 2 : 1, UNBOUNDED];
}

/** CR 509.1b, applied to a whole declaration. */
function blocksAreLegal(assignment: Assignment, bounds: ReadonlyMap<ObjectId, [number, number]>): boolean {
  const counts = new Map<ObjectId, number>();
  for (const attackerId of assignment.values()) counts.set(attackerId, (counts.get(attackerId) ?? 0) + 1);
  for (const [attackerId, count] of counts) {
    const [low, high] = bounds.get(attackerId) ?? [1, UNBOUNDED];
    if (!(low <= count && count <= high)) return false;
  }
  return true;
}

/**
 * The CR 509.1c maximisation, as a bounded search: creatures in the order
 * the defender's declaration suggests, branches that cannot beat the best
 * abandoned, the whole capped (the best found is still legal).
 */
function maximiseBlocks(
  obligations: readonly BlockObligation[],
  options: ReadonlyMap<ObjectId, readonly ObjectId[]>,
  bounds: ReadonlyMap<ObjectId, [number, number]>,
  preferred: ReadonlyMap<ObjectId, ObjectId | null>,
): Map<ObjectId, ObjectId> {
  const order = [...options.keys()].sort((a, b) => {
    const pa = (preferred.get(a) ?? null) === null ? 1 : 0;
    const pb = (preferred.get(b) ?? null) === null ? 1 : 0;
    return pa - pb || a - b;
  });
  const total = obligations.length;
  let best = new Map<ObjectId, ObjectId>();
  let bestCount = 0;
  if (blocksAreLegal(new Map(), bounds)) bestCount = countObeyed(obligations, new Map());
  const assignment = new Map<ObjectId, ObjectId>();
  let budget = SEARCH_BUDGET;

  const walk = (index: number): void => {
    if (budget <= 0 || bestCount >= total) return;
    budget -= 1;
    if (index === order.length) {
      if (!blocksAreLegal(assignment, bounds)) return;
      const obeyed = countObeyed(obligations, assignment);
      if (obeyed > bestCount) {
        bestCount = obeyed;
        best = new Map(assignment);
      }
      return;
    }
    let ceiling = countObeyed(obligations, assignment);
    const free = order.slice(index);
    for (const o of obligations) {
      if (obligationSatisfied(o, assignment)) continue;
      if (obligationReachable(o, free, options)) ceiling += 1;
    }
    if (ceiling <= bestCount) return;
    const blockerId = order[index]!;
    const chosen = preferred.get(blockerId) ?? null;
    const choices: (ObjectId | null)[] = [];
    if (chosen !== null) choices.push(chosen);
    choices.push(null);
    for (const attackerId of options.get(blockerId)!) if (attackerId !== chosen) choices.push(attackerId);
    for (const choice of choices) {
      if (choice === null) assignment.delete(blockerId);
      else assignment.set(blockerId, choice);
      walk(index + 1);
      assignment.delete(blockerId);
    }
  };
  walk(0);
  return best;
}

function countObeyed(obligations: readonly BlockObligation[], assignment: Assignment): number {
  return obligations.filter((o) => obligationSatisfied(o, assignment)).length;
}

/** A requirement from a resolved spell that outlives its source ("creatures block this turn if able"). */
export function registerStandingRequirement(game: Game, r: Requirement): Requirement {
  game.standingRequirements.push(r);
  return r;
}
