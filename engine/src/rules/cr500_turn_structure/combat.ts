/**
 * Combat (CR 506-511). Ported from
 * mtgfish/rules/cr500_turn_structure/cr506_combat.py.
 *
 * Restrictions and requirements (CR 508.1d, 509.1c): a declaration obeys
 * every restriction and, among those that do, the most requirements.
 * Damage division (CR 510.1c-d): no damage assignment order and no
 * lethal-first rule, except that trample spills only past lethal damage
 * (CR 702.19b; deathtouch makes 1 lethal, 702.2b); banding moves the choice
 * to the defender (702.22j). First strike creates a second damage step
 * (CR 510.4), checked at that moment. Blocked stays blocked (CR 509.1h).
 */

import type { Ability, Effect } from "../../ir";
import { CardType, Color, Duration, Phase, Step, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import {
  agentFor,
  invalidateAllCharacteristics,
  logRecord,
  opponents,
  permanents,
  player as playerOf,
  type Game,
} from "../../kernel/game";
import { isPermanent, objectText, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { dealDamage, removeFromCombat, tap } from "../cr100_game_concepts/actions";
import { runPriority } from "../cr100_game_concepts/priority";
import { protectedFrom } from "../cr100_game_concepts/protection";
import { Rule, ruleIsSuspended } from "../cr100_game_concepts/rule_overrides";
import { hasKeyword, hasType, isCreature, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { pendingTrigger, type PendingTrigger } from "../cr600_spells_and_abilities/triggers";
import { combatDamageToInitiativeHolder, combatDamageToMonarch } from "../cr700_additional_rules/designations";
import { emit } from "../emit";
import { ir, num } from "../ir_read";
import { costText, isEmpty, type ManaCost } from "../mana/cost";
import { affordable, attackCost, blockCost, pay } from "./combat_costs";
import { Act, enforceBlockRequirements, prohibited } from "./restrictions";

const STATIC: number = ir("AbilityKind.STATIC");

/**
 * The state of one combat phase. Plain data (Maps and Sets), so `cloneGame`'s
 * structuredClone copies it; matching reads it as `CombatView`.
 */
export interface Combat {
  /**
   * Attacker -> the player defending against it (its controller or protector
   * when a planeswalker or battle is attacked). Never a player-or-object
   * field: both are small integers.
   */
  attacking: Map<ObjectId, PlayerId>;
  /** Attacker -> the planeswalker or battle it attacks (CR 508.1a); NO_OBJECT once that left combat (CR 506.4c). */
  attackingPermanent: Map<ObjectId, ObjectId>;
  /** Attacker -> its blockers in declaration order (only so the default division replays the same). */
  blockers: Map<ObjectId, ObjectId[]>;
  /** Blocker -> the attackers it blocks. */
  blocking: Map<ObjectId, ObjectId[]>;
  /** Attackers that were blocked, even if every blocker has gone (CR 509.1h). */
  wasBlocked: Set<ObjectId>;
  /** CR 509.1h: blocked or unblocked only once blockers are declared. */
  blockersDeclared: boolean;
  /** Who controlled each permanent in combat when it joined (CR 506.4). */
  controllers: Map<ObjectId, PlayerId>;
  /** CR 702.49c: what each attacker that left combat was attacking: [player, permanent]. */
  leftAttacking: Map<ObjectId, [PlayerId, ObjectId]>;
}

export function newCombat(): Combat {
  return {
    attacking: new Map(),
    attackingPermanent: new Map(),
    blockers: new Map(),
    blocking: new Map(),
    wasBlocked: new Set(),
    blockersDeclared: false,
    controllers: new Map(),
    leftAttacking: new Map(),
  };
}

export const isAttacking = (combat: Combat, id: ObjectId): boolean => combat.attacking.has(id);
export const isBlocking = (combat: Combat, id: ObjectId): boolean => (combat.blocking.get(id) ?? []).length > 0;
export const isBlocked = (combat: Combat, id: ObjectId): boolean => combat.wasBlocked.has(id);

/** CR 506.4: take a permanent out of combat entirely. */
export function combatRemove(combat: Combat, id: ObjectId): void {
  stopAttackingOrBlocking(combat, id);
  stopBeingAttacked(combat, id);
  combat.controllers.delete(id);
}

/** CR 506.4: it stops being an attacking, blocking, blocked and/or unblocked creature. */
export function stopAttackingOrBlocking(combat: Combat, id: ObjectId): void {
  if (combat.attacking.has(id)) {
    combat.leftAttacking.set(id, [combat.attacking.get(id)!, combat.attackingPermanent.get(id) ?? NO_OBJECT]);
  }
  combat.attacking.delete(id);
  combat.attackingPermanent.delete(id);
  combat.wasBlocked.delete(id);
  for (const list of combat.blockers.values()) removeFirst(list, id);
  combat.blockers.delete(id);
  for (const list of combat.blocking.values()) removeFirst(list, id);
  combat.blocking.delete(id);
}

/** CR 506.4c: a planeswalker or battle stops being attacked; its attackers attack nothing. */
export function stopBeingAttacked(combat: Combat, id: ObjectId): void {
  for (const [attacker, attacked] of combat.attackingPermanent) {
    if (attacked === id) combat.attackingPermanent.set(attacker, NO_OBJECT);
  }
}

function clearCombat(combat: Combat): void {
  combat.attacking.clear();
  combat.attackingPermanent.clear();
  combat.leftAttacking.clear();
  combat.blockers.clear();
  combat.blocking.clear();
  combat.wasBlocked.clear();
  combat.controllers.clear();
  combat.blockersDeclared = false;
}

function removeFirst(list: ObjectId[], id: ObjectId): void {
  const index = list.indexOf(id);
  if (index >= 0) list.splice(index, 1);
}

/** Attack a planeswalker or battle rather than a player. */
export interface AttackPermanent {
  readonly permanent: ObjectId;
}
/** A bare number always means a player, never an object. */
export type Defender = PlayerId | AttackPermanent;

/** A damage recipient: a permanent, or a player id. */
export type Recipient = GameObject | PlayerId;

/** The agent's combat choices; each optional, defaulted when absent (Python's hasattr). */
export interface CombatAgent {
  declareAttackers?(game: Game, player: PlayerId, candidates: readonly GameObject[]): ReadonlyMap<ObjectId, Defender> | null;
  declareBlockers?(game: Game, player: PlayerId, combat: Combat, available: readonly GameObject[]): ReadonlyMap<ObjectId, readonly ObjectId[]> | null;
  /** A division keyed by index into `recipients`. */
  assignCombatDamage?(game: Game, chooser: PlayerId, source: ObjectId, recipients: readonly Recipient[], total: number): ReadonlyMap<number, number> | null;
  chooseEnlist?(game: Game, player: PlayerId, attacker: GameObject, eligible: readonly GameObject[]): ObjectId | null;
}

const charsOf = (game: Game, obj: GameObject): Characteristics => characteristicsOf(game, obj);
const textOf = (game: Game, obj: GameObject): string => objectText(obj, charsOf(game, obj).name);
const sortedIds = (ids: Iterable<ObjectId>): ObjectId[] => [...ids].sort((a, b) => a - b);

export function attackers(game: Game): ReadonlyMap<ObjectId, PlayerId> {
  return (game.combat as Combat | null)?.attacking ?? new Map();
}

// -- declaring attackers (CR 508)

/** CR 508.1: the active player declares attackers, as a turn-based action. */
export function declareAttackers(game: Game): void {
  const combat = combatOf(game);
  clearCombat(combat);
  const active = game.activePlayer;
  const candidates = permanents(game, active).filter((obj) => canAttack(game, obj));
  if (candidates.length === 0) {
    emit(game, event(EventKind.ATTACKERS_DECLARED, { player: active, amount: 0 }));
    return;
  }
  const agent = agentFor(game, active) as CombatAgent | null | undefined;
  let proposal = new Map<ObjectId, Defender>();
  if (agent?.declareAttackers !== undefined) proposal = new Map(agent.declareAttackers(game, active, candidates) ?? []);

  proposal = enforceAttackRequirements(game, active, candidates, proposal);
  const costs = attackCosts(game, active, proposal);

  const tappedHere: GameObject[] = [];
  for (const attackerId of sortedIds(proposal.keys())) {
    const obj = game.objects.get(attackerId);
    if (obj === undefined) continue;
    const [player, permanent] = resolveDefender(game, proposal.get(attackerId)!);
    combat.attacking.set(attackerId, player);
    // CR 506.4 watches for a change of controller.
    combat.controllers.set(attackerId, obj.controller);
    if (permanent !== NO_OBJECT) {
      combat.attackingPermanent.set(attackerId, permanent);
      const attacked = game.objects.get(permanent);
      if (attacked !== undefined) combat.controllers.set(permanent, attacked.controller);
    }
    obj.attackedThisTurn = true;
    // CR 508.1f: attacking taps it, unless vigilance or the rule is off (CR 101.1).
    if (!hasKeyword(charsOf(game, obj), "Vigilance") && ruleIsSuspended(game, Rule.ATTACKING_TAPS, { obj }) === null) {
      if (!obj.tapped) tappedHere.push(obj);
      obj.tapped = true;
    }
  }

  // CR 508.1g-j: enlist first, then the total cost to attack, all at once;
  // creatures whose cost goes unpaid don't attack and are untapped again.
  const enlisted = payEnlistCosts(game, combat, active, agent);
  if (costs.size > 0 && !pay(game, active, costs, "attack")) {
    for (const attackerId of costs.keys()) {
      for (const [creature, pending] of enlisted.get(attackerId) ?? []) {
        creature.tapped = false;
        const index = (game.pendingTriggers as PendingTrigger[]).indexOf(pending);
        if (index >= 0) game.pendingTriggers.splice(index, 1);
      }
      combat.attacking.delete(attackerId);
      combat.attackingPermanent.delete(attackerId);
      combat.controllers.delete(attackerId);
      const obj = game.objects.get(attackerId);
      if (obj === undefined) continue;
      obj.attackedThisTurn = false;
      if (tappedHere.includes(obj)) obj.tapped = false;
      logRecord(game, `${textOf(game, obj)} does not attack: its cost was not paid`, { kind: "combat" });
    }
  }

  invalidateAllCharacteristics(game);
  for (const attackerId of sortedIds(combat.attacking.keys())) {
    emit(game, event(EventKind.ATTACKS, { objectId: attackerId, player: active, amount: combat.attacking.get(attackerId)! }));
  }
  emit(game, event(EventKind.ATTACKERS_DECLARED, { player: active, amount: combat.attacking.size }));
}

/**
 * CR 508.1h: the cost of each chosen attacker, with `proposal` cut down to
 * what the active player will pay for (mutated).
 */
function attackCosts(game: Game, active: PlayerId, proposal: Map<ObjectId, Defender>): Map<ObjectId, ManaCost> {
  const costs = new Map<ObjectId, ManaCost>();
  for (const [attackerId, defender] of proposal) {
    const obj = game.objects.get(attackerId);
    if (obj === undefined) continue;
    const [player, permanent] = resolveDefender(game, defender);
    const mana = attackCost(game, obj, player, permanent);
    if (!isEmpty(mana)) costs.set(attackerId, mana);
  }
  if (costs.size === 0) return new Map();
  // CR 508.1f taps every attacker without vigilance before the cost is paid.
  const tapping = [...proposal.keys()].filter((id) => {
    const obj = game.objects.get(id);
    return obj !== undefined && !hasKeyword(charsOf(game, obj), "Vigilance") && ruleIsSuspended(game, Rule.ATTACKING_TAPS, { obj }) === null;
  });
  const paid = affordable(game, active, costs, { tapped: tapping });
  for (const [attackerId, mana] of costs) {
    if (paid.has(attackerId)) continue;
    proposal.delete(attackerId);
    logRecord(game, `${textOf(game, game.objects.get(attackerId)!)} does not attack: its cost to attack (${costText(mana)}) is not paid`, {
      kind: "combat",
    });
  }
  return paid;
}

/**
 * CR 702.154a-b, 508.1g-h: enlist, an optional cost to attack. Tapping a
 * creature for it makes the linked "when you do" trigger (CR 603.9): +X/+0,
 * X the enlisted creature's power as it resolves. An agent without
 * `chooseEnlist` never enlists.
 */
function payEnlistCosts(
  game: Game,
  combat: Combat,
  active: PlayerId,
  agent: CombatAgent | null | undefined,
): Map<ObjectId, [GameObject, PendingTrigger][]> {
  const paid = new Map<ObjectId, [GameObject, PendingTrigger][]>();
  if (agent?.chooseEnlist === undefined) return paid;
  for (const attackerId of sortedIds(combat.attacking.keys())) {
    const attacker = game.objects.get(attackerId);
    if (attacker === undefined) continue;
    const instances = charsOf(game, attacker).abilities.filter(
      (a) => (a.keyword ?? "").toLowerCase() === "enlist" && num(a.kind) === STATIC,
    ).length;
    for (let i = 0; i < instances; i += 1) {
      const eligible = permanents(game, active).filter((obj) => {
        const chars = charsOf(game, obj);
        return !combat.attacking.has(obj.id) && !obj.tapped && isCreature(chars) && (!obj.summoningSick || hasKeyword(chars, "Haste"));
      });
      if (eligible.length === 0) break;
      const chosen = agent.chooseEnlist(game, active, attacker, eligible);
      const enlisted = eligible.find((obj) => obj.id === chosen);
      if (enlisted === undefined || !tap(game, enlisted, { source: attackerId })) continue;
      logRecord(game, `${textOf(game, attacker)} enlists ${textOf(game, enlisted)}`, { kind: "cost" });
      const boost: Effect = {
        $: "Effect",
        kind: ir("EffectKind.MODIFY_PT"),
        amount: { $: "Value", kind: ir("ValueKind.POWER"), filter: { $: "ObjectFilter", specific: [enlisted.id], zones: { $set: [] } } },
        amount2: { $: "Value", constant: 0 },
        duration: Duration.END_OF_TURN as never,
        text: "this creature gets +X/+0 until end of turn, where X is the tapped creature's power",
      };
      const ability: Ability = {
        $: "Ability",
        kind: ir("AbilityKind.TRIGGERED"),
        effects: [boost],
        keyword: "Enlist",
        text: "when you do (enlist)",
      };
      const pending = pendingTrigger(attackerId, ability, event(EventKind.ABILITY_TRIGGERED, { objectId: attackerId }), active);
      game.pendingTriggers.push(pending);
      let list = paid.get(attackerId);
      if (list === undefined) paid.set(attackerId, (list = []));
      list.push([enlisted, pending]);
    }
  }
  return paid;
}

/** CR 508.1a: the basic legality of one creature attacking. */
export function canAttack(game: Game, obj: GameObject): boolean {
  const chars = charsOf(game, obj);
  if (!isCreature(chars)) return false;
  if (obj.tapped) return false;
  // CR 302.6, unless haste or the rule is off (CR 101.1).
  if (obj.summoningSick && !hasKeyword(chars, "Haste") && ruleIsSuspended(game, Rule.SUMMONING_SICKNESS, { obj }) === null) return false;
  // CR 702.3b
  if (hasKeyword(chars, "Defender")) return false;
  // CR 101.2: any "can't attack" beats every permission.
  return prohibited(game, Act.ATTACK, { obj }) === null;
}

/**
 * CR 508.1d-e: restrictions drop offending attackers; requirements add any
 * creature that must attack and legally can, where no cost is involved.
 */
function enforceAttackRequirements(
  game: Game,
  active: PlayerId,
  candidates: readonly GameObject[],
  proposal: ReadonlyMap<ObjectId, Defender>,
): Map<ObjectId, Defender> {
  const result = new Map<ObjectId, Defender>();
  for (const [attackerId, defender] of proposal) {
    const obj = game.objects.get(attackerId);
    // KNOWN: F61 - Python indexes game.objects[attacker_id] and raises on an id that is not there.
    if (obj !== undefined && attackIsPermitted(game, obj, defender)) result.set(attackerId, defender);
  }
  for (const obj of candidates) {
    if (result.has(obj.id)) continue;
    if (!mustAttack(game, obj)) continue;
    const forced = forcedDefender(game, obj, active);
    if (forced === null) continue;
    // CR 508.1d: a defender that would cost something gives way to one that does not.
    const options = [forced, ...opponents(game, active).filter((p) => p !== forced)];
    const defender = options.find((option) => attackIsPermitted(game, obj, option) && !costsToAttack(game, obj, option));
    if (defender !== undefined) {
      result.set(obj.id, defender);
      logRecord(game, `${textOf(game, obj)} must attack and is forced into combat`, { kind: "combat" });
    }
  }
  return result;
}

/** CR 508.1d: never forced to pay to attack. */
function costsToAttack(game: Game, obj: GameObject, defender: Defender): boolean {
  const [player, permanent] = resolveDefender(game, defender);
  return !isEmpty(attackCost(game, obj, player, permanent));
}

/** CR 508.1e: goad (CR 701.38) and "attacks each combat if able". */
function mustAttack(game: Game, obj: GameObject): boolean {
  if (obj.goadedBy.size > 0) return true;
  return hasKeyword(charsOf(game, obj), "Attacks each combat if able");
}

/** A goaded creature attacks a player who did not goad it if it can (CR 701.38b). */
function forcedDefender(game: Game, obj: GameObject, active: PlayerId): PlayerId | null {
  const others = opponents(game, active);
  if (others.length === 0) return null;
  if (obj.goadedBy.size > 0) {
    const preferred = others.filter((p) => !obj.goadedBy.has(p));
    if (preferred.length > 0) return preferred[0]!;
  }
  return others[0]!;
}

/** Restrictions on attacking (CR 508.1c-d). */
function attackIsPermitted(game: Game, obj: GameObject, defender: Defender): boolean {
  if (!canAttack(game, obj)) return false;
  if (!mayBeAttacked(game, obj, defender)) return false;
  const [player, permanent] = resolveDefender(game, defender);
  if (permanent === NO_OBJECT) {
    if (prohibited(game, Act.ATTACK_PLAYER, { obj, player }) !== null) return false;
  } else if (prohibited(game, Act.ATTACK_PLANESWALKER, { obj, counterpart: game.objects.get(permanent) ?? null }) !== null) {
    return false;
  }
  if (hasKeyword(charsOf(game, obj), "Can't attack")) return false;
  // A goaded creature can't attack a goader if another choice exists.
  if (obj.goadedBy.size > 0 && typeof defender === "number" && obj.goadedBy.has(defender)) {
    if (opponents(game, obj.controller).some((p) => !obj.goadedBy.has(p))) return false;
  }
  return true;
}

/**
 * CR 506.2, 802.1, 903.2: any opponent, a planeswalker they control or a
 * battle they protect - never yourself or a player who has left (CR 800.4).
 */
function mayBeAttacked(game: Game, obj: GameObject, defender: Defender): boolean {
  const [player, permanent] = resolveDefender(game, defender);
  if (permanent !== NO_OBJECT) {
    const attacked = game.objects.get(permanent);
    if (attacked === undefined || !isPermanent(attacked)) return false;
    const chars = charsOf(game, attacked);
    if (!(hasType(chars, CardType.PLANESWALKER) || hasType(chars, CardType.BATTLE))) return false;
  }
  return opponents(game, obj.controller).includes(player);
}

/** Who is defending, and what permanent (if any) is attacked. */
function resolveDefender(game: Game, defender: Defender): [PlayerId, ObjectId] {
  if (typeof defender !== "number") {
    const obj = game.objects.get(defender.permanent);
    if (obj !== undefined && isPermanent(obj)) return [defendingPlayer(game, obj), obj.id];
    return [0, NO_OBJECT];
  }
  return [Math.trunc(defender), NO_OBJECT];
}

/** Who defends this permanent: a planeswalker's controller, a battle's protector (CR 506.2, 310.9d). */
export function defendingPlayer(game: Game, attacked: GameObject): PlayerId {
  if (hasType(charsOf(game, attacked), CardType.BATTLE) && attacked.protector !== NO_PLAYER) return attacked.protector;
  return attacked.controller;
}

/**
 * Make a permanent just put onto the battlefield an attacker (CR 508.4).
 * What it attacks: what the first of `like` attacked (ninjutsu, CR
 * 702.49c), else the first opponent still in the game. CR 506.3a-c: a
 * noncreature, a creature not the attacking player's, or one headed for
 * something gone, enters but never attacks. Returns whether it attacks.
 */
export function enterAttacking(game: Game, permanent: GameObject, options: { like?: readonly ObjectId[] } = {}): boolean {
  if (game.phase !== Phase.COMBAT || !isPermanent(permanent)) return false;
  if (!isCreature(charsOf(game, permanent))) return false;
  if (permanent.controller !== game.activePlayer) return false;

  const combat = combatOf(game);
  let defender: PlayerId | null = null;
  let attacked: ObjectId = NO_OBJECT;
  for (const objectId of options.like ?? []) {
    if (combat.attacking.has(objectId)) {
      defender = combat.attacking.get(objectId)!;
      attacked = combat.attackingPermanent.get(objectId) ?? NO_OBJECT;
      break;
    }
    // CR 702.49c: one that already left combat, by what it was attacking.
    const left = combat.leftAttacking.get(objectId);
    if (left !== undefined) {
      [defender, attacked] = left;
      break;
    }
  }
  if (defender === null) {
    const others = opponents(game, permanent.controller);
    if (others.length === 0) return false;
    defender = others[0]!;
  }

  let target: GameObject | undefined;
  if (attacked !== NO_OBJECT) {
    target = game.objects.get(attacked);
    if (target === undefined || !isPermanent(target)) return false;
    const targetChars = charsOf(game, target);
    if (!(hasType(targetChars, CardType.PLANESWALKER) || hasType(targetChars, CardType.BATTLE))) return false;
    // CR 508.4a: still a defending player's to attack.
    if (target.controller === permanent.controller) return false;
    defender = target.controller;
  } else if (playerOf(game, defender).hasLost) {
    return false;
  }

  combat.attacking.set(permanent.id, defender);
  combat.controllers.set(permanent.id, permanent.controller);
  if (attacked !== NO_OBJECT) {
    combat.attackingPermanent.set(permanent.id, attacked);
    combat.controllers.set(attacked, target!.controller);
  }
  invalidateAllCharacteristics(game);
  return true;
}

// -- declaring blockers (CR 509)

/** CR 509.1: each defending player declares blockers, all at once. */
export function declareBlockers(game: Game): void {
  const combat = combatOf(game);
  // CR 506.4: what stopped belonging in combat is out before anyone blocks.
  checkRemovalFromCombat(game);
  // CR 509.1h: every attacker is now blocked or unblocked.
  combat.blockersDeclared = true;
  if (combat.attacking.size === 0) {
    emit(game, event(EventKind.BLOCKERS_DECLARED, { player: game.activePlayer }));
    return;
  }

  const defenders = [...new Set(combat.attacking.values())].sort((a, b) => a - b);
  for (const defenderId of defenders) {
    const agent = agentFor(game, defenderId) as CombatAgent | null | undefined;
    if (agent?.declareBlockers === undefined) continue;
    const available = permanents(game, defenderId).filter((obj) => canBlockAtAll(game, obj));
    let proposal = new Map<ObjectId, readonly ObjectId[]>(agent.declareBlockers(game, defenderId, combat, available) ?? []);
    // CR 509.1c: the most requirements, the player's own choices kept where
    // compatible; never forced to pay to block.
    const costs = new Map(available.map((obj) => [obj.id, blockCost(game, obj)] as const));
    proposal = enforceBlockRequirements(
      game,
      combat,
      defenderId,
      available.filter((obj) => proposal.has(obj.id) || isEmpty(costs.get(obj.id)!)),
      proposal,
    );
    // CR 509.1d-f: the total cost to block, paid all at once.
    const wanted = new Map<ObjectId, ManaCost>();
    for (const blockerId of proposal.keys()) {
      const cost = costs.get(blockerId);
      if (cost !== undefined && !isEmpty(cost)) wanted.set(blockerId, cost);
    }
    let paid = wanted.size > 0 ? affordable(game, defenderId, wanted) : new Map<ObjectId, ManaCost>();
    if (paid.size > 0 && !pay(game, defenderId, paid, "block")) paid = new Map();
    for (const [blockerId, cost] of wanted) {
      if (paid.has(blockerId)) continue;
      proposal = new Map([...proposal].filter(([k]) => k !== blockerId));
      logRecord(game, `${textOf(game, game.objects.get(blockerId)!)} does not block: its cost to block (${costText(cost)}) is not paid`, {
        kind: "combat",
      });
    }
    for (const blockerId of sortedIds(proposal.keys())) {
      const blocker = game.objects.get(blockerId);
      if (blocker === undefined || !canBlockAtAll(game, blocker)) continue;
      const legal = proposal
        .get(blockerId)!
        .filter((attackerId) => combat.attacking.has(attackerId) && canBlock(game, blocker, game.objects.get(attackerId)!));
      if (legal.length === 0) continue;
      combat.blocking.set(blockerId, legal);
      // CR 506.4 again: a blocker that changes controller leaves combat.
      combat.controllers.set(blockerId, blocker.controller);
      for (const attackerId of legal) {
        let list = combat.blockers.get(attackerId);
        if (list === undefined) combat.blockers.set(attackerId, (list = []));
        list.push(blockerId);
      }
    }
  }

  enforceMenace(game, combat);

  for (const [attackerId, blockers] of combat.blockers) {
    if (blockers.length === 0) continue;
    combat.wasBlocked.add(attackerId);
    const attacker = game.objects.get(attackerId);
    if (attacker !== undefined) attacker.blockedThisTurn = true;
    emit(game, event(EventKind.BECOMES_BLOCKED, { objectId: attackerId, amount: blockers.length }));
    // CR 509.3d: once for each creature that blocks it.
    for (const blockerId of blockers) {
      emit(
        game,
        event(EventKind.BECOMES_BLOCKED_BY, {
          objectId: attackerId,
          source: blockerId,
          sourceController: combat.controllers.get(blockerId) ?? NO_PLAYER,
        }),
      );
    }
  }
  for (const blockerId of sortedIds(combat.blocking.keys())) emit(game, event(EventKind.BLOCKS, { objectId: blockerId }));
  // CR 509.3g: an attacker with no blocker declared for it.
  for (const attackerId of sortedIds(combat.attacking.keys())) {
    if ((combat.blockers.get(attackerId) ?? []).length === 0) emit(game, event(EventKind.ATTACKS_UNBLOCKED, { objectId: attackerId }));
  }
  emit(game, event(EventKind.BLOCKERS_DECLARED, { player: game.activePlayer }));
}

/** CR 509.1a: an untapped creature can block; summoning sickness doesn't matter. */
export function canBlockAtAll(game: Game, obj: GameObject): boolean {
  return isCreature(charsOf(game, obj)) && !obj.tapped;
}

/** Whether this blocker may block this attacker (CR 509.1b): evasion lives here. */
export function canBlock(game: Game, blocker: GameObject, attacker: GameObject): boolean {
  const attackerChars = charsOf(game, attacker);
  const blockerChars = charsOf(game, blocker);
  const a = (k: string): boolean => hasKeyword(attackerChars, k);
  const b = (k: string): boolean => hasKeyword(blockerChars, k);

  // CR 101.2: "can't block" and "can't be blocked" win outright.
  if (prohibited(game, Act.BLOCK, { obj: blocker, counterpart: attacker }) !== null) return false;
  if (prohibited(game, Act.BE_BLOCKED, { obj: attacker, counterpart: blocker }) !== null) return false;
  if (a("Unblockable")) return false;
  // CR 702.9b
  if (a("Flying") && !(b("Flying") || b("Reach"))) return false;
  // CR 702.20b: shadow blocks and is blocked only by shadow.
  if (a("Shadow") !== b("Shadow") && (a("Shadow") || b("Shadow"))) return false;
  // CR 702.5b
  if (a("Horsemanship") && !b("Horsemanship")) return false;
  // CR 702.16f
  if (protectedFrom(game, attacker, blocker)) return false;
  // CR 702.15b: landwalk is a property of the defender's board.
  if (hasUnblockableLandwalk(game, attacker, blocker.controller)) return false;
  // CR 701.54c: the Ring-bearer can't be blocked by greater power, as skulk.
  if (isARingBearer(game, attacker) && (blockerChars.power ?? 0) > (attackerChars.power ?? 0)) return false;
  // CR 702.118b
  if (a("Skulk") && (blockerChars.power ?? 0) > (attackerChars.power ?? 0)) return false;
  // CR 702.17b
  if (a("Fear") && !(hasType(blockerChars, CardType.ARTIFACT) || blockerChars.colors & Color.BLACK)) return false;
  // CR 702.13b
  if (a("Intimidate") && !(hasType(blockerChars, CardType.ARTIFACT) || blockerChars.colors & attackerChars.colors)) return false;
  return true;
}

/**
 * CR 701.54a/e: whether this is its controller's Ring-bearer; a stale
 * designation is cleared as it is seen. Python cr701_ring.is_a_ring_bearer
 * (the parser's module, not yet published), inlined.
 */
function isARingBearer(game: Game, obj: GameObject): boolean {
  if (obj.controller < 0 || obj.controller >= game.players.length) return false;
  const p = playerOf(game, obj.controller);
  const bearer = game.objects.get(p.ringBearer);
  if (bearer === undefined) return false;
  if (bearer.supersededBy !== NO_OBJECT || bearer.zone !== Zone.BATTLEFIELD || bearer.controller !== p.id) {
    p.ringBearer = NO_OBJECT;
    return false;
  }
  return bearer.id === obj.id;
}

/** CR 702.15b: does the defending player control a land of the walked type? */
function hasUnblockableLandwalk(game: Game, attacker: GameObject, defender: PlayerId): boolean {
  const hooks = matchHooks();
  for (const ability of charsOf(game, attacker).abilities) {
    if (!(ability.keyword ?? "").toLowerCase().endsWith("walk")) continue;
    const quality = ability.quality;
    if (quality == null || (quality as { $: string }).$ !== "ObjectFilter") continue;
    for (const permanent of permanents(game, defender)) {
      if (hooks.matches(game, permanent, quality as never, {})) return true;
    }
  }
  return false;
}

/** CR 702.111b: a single blocker on a menace attacker is an illegal block, removed. */
function enforceMenace(game: Game, combat: Combat): void {
  for (const [attackerId, blockers] of [...combat.blockers]) {
    const attacker = game.objects.get(attackerId);
    if (attacker === undefined || !hasKeyword(charsOf(game, attacker), "Menace")) continue;
    if (blockers.length > 0 && blockers.length < 2) {
      for (const blockerId of blockers) {
        const blocked = combat.blocking.get(blockerId) ?? [];
        removeFirst(blocked, attackerId);
        if (blocked.length === 0) combat.blocking.delete(blockerId);
      }
      combat.blockers.set(attackerId, []);
    }
  }
}

/** CR 702.22b: banding, or "bands with other". */
function hasBanding(game: Game, objectId: ObjectId): boolean {
  const obj = game.objects.get(objectId);
  if (obj === undefined) return false;
  const chars = charsOf(game, obj);
  return hasKeyword(chars, "Banding") || chars.abilities.some((a) => (a.keyword ?? "").toLowerCase().startsWith("bands with other"));
}

/** Who this creature attacks, for the banding swap. */
function defendingPlayerOf(game: Game, combat: Combat, attackerId: ObjectId): PlayerId {
  const defender = combat.attacking.get(attackerId);
  if (defender !== undefined) return defender;
  const target = combat.attackingPermanent.get(attackerId);
  if (target !== undefined) {
    const obj = game.objects.get(target);
    if (obj !== undefined) return obj.controller;
  }
  return game.activePlayer;
}

// -- removal from combat (CR 506.4)

/**
 * CR 506.4: take out of combat everything that no longer belongs there.
 * Swept at each moment the record is read, which is indistinguishable from
 * removal as it happens. Returns what left combat.
 */
export function checkRemovalFromCombat(game: Game): ObjectId[] {
  const combat = game.combat as Combat | null;
  if (combat == null || !(combat.attacking.size > 0 || combat.blocking.size > 0)) return [];
  const fighters = [...combat.attacking.keys(), ...combat.blocking.keys()];
  const attacked = [...combat.attackingPermanent.values()].filter((id) => id !== NO_OBJECT);
  const removed: ObjectId[] = [];
  for (const objectId of new Set([...fighters, ...attacked])) {
    const obj = game.objects.get(objectId);
    if (obj === undefined) {
      combatRemove(combat, objectId);
      removed.push(objectId);
      continue;
    }
    if (hasLeftTheGameState(combat, obj)) {
      removeFromCombat(game, obj);
      removed.push(objectId);
      continue;
    }
    const chars = charsOf(game, obj);
    let left = false;
    // An attacker or blocker that stops being a creature, or becomes a battle.
    if (fighters.includes(objectId) && !(isCreature(chars) && !hasType(chars, CardType.BATTLE))) {
      stopAttackingOrBlocking(combat, objectId);
      left = true;
    }
    // A planeswalker or battle that stops being one stops being attacked.
    if (attacked.includes(objectId) && !(hasType(chars, CardType.PLANESWALKER) || hasType(chars, CardType.BATTLE))) {
      stopBeingAttacked(combat, objectId);
      left = true;
    }
    if (left) {
      combat.controllers.delete(objectId);
      removed.push(objectId);
      logRecord(game, `${textOf(game, obj)} is removed from combat`, { kind: "combat" });
      invalidateAllCharacteristics(game);
    }
  }
  return removed;
}

/** Leaving the battlefield, phasing out (CR 702.26b) or changing controller. */
function hasLeftTheGameState(combat: Combat, obj: GameObject): boolean {
  if (!isPermanent(obj) || obj.phasedOut) return true;
  const was = combat.controllers.get(obj.id);
  return was !== undefined && was !== obj.controller;
}

// -- combat damage (CR 510)

type Assignment = [GameObject, Recipient, number, boolean, boolean];

/** Assign and deal combat damage (CR 510.1-2), all simultaneously. */
export function dealCombatDamage(game: Game, options: { firstStrikeStep?: boolean } = {}): void {
  const combat = combatOf(game);
  checkRemovalFromCombat(game); // CR 506.4
  if (combat.attacking.size === 0) return;
  if (!options.firstStrikeStep && needsFirstStrikeStep(game, combat)) {
    // CR 510.4: an extra damage step, taken before this one.
    const previous = game.step;
    game.step = Step.FIRST_STRIKE_COMBAT_DAMAGE;
    damageRound(game, combat, true);
    game.step = previous;
    runPriority(game);
    if (game.gameOver) return;
  }
  damageRound(game, combat, false);
}

function needsFirstStrikeStep(game: Game, combat: Combat): boolean {
  for (const objectId of [...combat.attacking.keys(), ...combat.blocking.keys()]) {
    const obj = game.objects.get(objectId);
    if (obj === undefined) continue;
    const chars = charsOf(game, obj);
    if (hasKeyword(chars, "First strike") || hasKeyword(chars, "Double strike")) return true;
  }
  return false;
}

/** Which creatures deal damage in which step; double strike deals in both (CR 702.4b). */
function dealsDamageNow(game: Game, obj: GameObject, firstStrike: boolean): boolean {
  const chars = charsOf(game, obj);
  const first = hasKeyword(chars, "First strike");
  const double = hasKeyword(chars, "Double strike");
  return firstStrike ? first || double : double || !first;
}

/** One damage step: every assignment worked out, then all dealt at once. */
function damageRound(game: Game, combat: Combat, firstStrike: boolean): void {
  // CR 506.4 once more, after the first-strike step and its priority round.
  checkRemovalFromCombat(game);
  const assignments: Assignment[] = [];
  // CR 702.19b: lethal is checked against what else is assigned this step.
  const marked = new Map<ObjectId, number>();
  const record = (made: Assignment[]): Assignment[] => {
    for (const [, who, amount] of made) if (typeof who !== "number") marked.set(who.id, (marked.get(who.id) ?? 0) + amount);
    return made;
  };
  for (const attackerId of sortedIds(combat.attacking.keys())) {
    const attacker = game.objects.get(attackerId);
    if (attacker === undefined || !isPermanent(attacker) || !dealsDamageNow(game, attacker, firstStrike)) continue;
    assignments.push(...record(attackerAssignment(game, combat, attacker, marked)));
  }
  for (const blockerId of sortedIds(combat.blocking.keys())) {
    const blocker = game.objects.get(blockerId);
    if (blocker === undefined || !isPermanent(blocker) || !dealsDamageNow(game, blocker, firstStrike)) continue;
    assignments.push(...record(blockerAssignment(game, combat, blocker, marked)));
  }

  // CR 726.2: who hit the initiative's holder, read before damage is dealt.
  const initiativeAttackers = new Map<PlayerId, PlayerId[]>();
  for (const [source, target, amount] of assignments) {
    if (typeof target === "number" && amount > 0 && playerOf(game, target).hasInitiative) {
      let hitters = initiativeAttackers.get(target);
      if (hitters === undefined) initiativeAttackers.set(target, (hitters = []));
      if (!hitters.includes(source.controller)) hitters.push(source.controller);
    }
  }
  for (const [source, target, amount, deathtouch, lifelink] of assignments) {
    // CR 725.4: combat damage to the monarch passes it on, before the damage.
    if (typeof target === "number" && amount > 0) combatDamageToMonarch(game, target, source.controller);
    dealDamage(game, target, amount, {
      source: source.id,
      sourceController: source.controller,
      deathtouch,
      lifelink,
      combat: true,
      isCommanderSource: source.isCommander,
    });
  }
  for (const [victim, hitters] of initiativeAttackers) combatDamageToInitiativeHolder(game, victim, hitters);
}

/** How one attacking creature divides its combat damage (CR 510.1b-c). */
function attackerAssignment(game: Game, combat: Combat, attacker: GameObject, marked: ReadonlyMap<ObjectId, number>): Assignment[] {
  const chars = charsOf(game, attacker);
  const power = chars.power ?? 0;
  // CR 510.1a
  if (power <= 0) return [];
  const deathtouch = hasKeyword(chars, "Deathtouch");
  const lifelink = hasKeyword(chars, "Lifelink");
  const trample = hasKeyword(chars, "Trample");
  const blockers = (combat.blockers.get(attacker.id) ?? [])
    .map((id) => game.objects.get(id))
    .filter((obj): obj is GameObject => obj !== undefined && isPermanent(obj));

  if (blockers.length === 0) {
    // CR 509.1h: blocked stays blocked, and assigns nothing - unless trample.
    if (isBlocked(combat, attacker.id) && !trample) return [];
    const target = damageRecipient(game, combat, attacker);
    return target === null ? [] : [[attacker, target, power, deathtouch, lifelink]];
  }

  // CR 702.19b: trample's excess goes last in `recipients`, at `spill`.
  const recipients: Recipient[] = [...blockers];
  let spill = -1;
  if (trample) {
    const target = damageRecipient(game, combat, attacker);
    if (target !== null) {
      spill = recipients.length;
      recipients.push(target);
    }
  }
  // CR 510.1c; CR 702.22j: a banding blocker hands the choice to the defender.
  let chooser = attacker.controller;
  if (blockers.some((b) => hasBanding(game, b.id))) chooser = defendingPlayerOf(game, combat, attacker.id);
  const lethal = blockers.map((b) => lethalDamage(game, b, { deathtouch, marked }));
  const division = divide(game, chooser, attacker, recipients, power, lethalFirst(power, lethal, spill), (choice) =>
    attackerDivisionIsLegal(choice, recipients.length, power, lethal, spill),
  );
  return asAssignments(attacker, recipients, division, deathtouch, lifelink);
}

/**
 * Lethal to each creature in turn, then the rest onward: the deterministic
 * default a replay needs, and the one that satisfies CR 702.19b.
 */
function lethalFirst(total: number, lethal: readonly number[], spill: number): Map<number, number> {
  const division = new Map<number, number>();
  let remaining = total;
  for (let index = 0; index < lethal.length; index += 1) {
    if (remaining <= 0) break;
    const assigned = Math.min(remaining, lethal[index]!);
    if (assigned > 0) {
      division.set(index, assigned);
      remaining -= assigned;
    }
  }
  if (remaining <= 0) return division;
  const last = spill >= 0 ? spill : lethal.length - 1;
  if (last >= 0) division.set(last, (division.get(last) ?? 0) + remaining);
  return division;
}

/** CR 510.1c and, with trample, CR 702.19b. */
function attackerDivisionIsLegal(
  division: ReadonlyMap<number, number>,
  count: number,
  total: number,
  lethal: readonly number[],
  spill: number,
): boolean {
  if (!divisionCovers(division, count, total)) return false;
  if (spill >= 0 && (division.get(spill) ?? 0) > 0) return lethal.every((needed, index) => (division.get(index) ?? 0) >= needed);
  return true;
}

/** Every point goes somewhere it may go (CR 510.1c-d). */
function divisionCovers(division: ReadonlyMap<number, number>, count: number, total: number): boolean {
  let sum = 0;
  for (const [k, v] of division) {
    if (!Number.isInteger(k) || k < 0 || k >= count) return false;
    if (!Number.isInteger(v) || v < 0) return false;
    sum += v;
  }
  return sum === total;
}

/**
 * Lethal damage for checking an assignment (CR 702.19b): marked damage and
 * damage assigned this step count; deathtouch makes 1 lethal (CR 702.2b).
 */
export function lethalDamage(
  game: Game,
  creature: GameObject,
  options: { deathtouch?: boolean; marked?: ReadonlyMap<ObjectId, number> } = {},
): number {
  if (options.deathtouch) return 1;
  const already = creature.damage + (options.marked?.get(creature.id) ?? 0);
  return Math.max(1, (charsOf(game, creature).toughness ?? 0) - already);
}

/** How one blocking creature divides its combat damage (CR 510.1d). */
function blockerAssignment(game: Game, combat: Combat, blocker: GameObject, marked: ReadonlyMap<ObjectId, number>): Assignment[] {
  const chars = charsOf(game, blocker);
  const power = chars.power ?? 0;
  if (power <= 0) return [];
  const deathtouch = hasKeyword(chars, "Deathtouch");
  const lifelink = hasKeyword(chars, "Lifelink");
  const blocked = (combat.blocking.get(blocker.id) ?? [])
    .map((id) => game.objects.get(id))
    .filter((obj): obj is GameObject => obj !== undefined && isPermanent(obj));
  if (blocked.length === 0) return [];
  if (blocked.length === 1) return [[blocker, blocked[0]!, power, deathtouch, lifelink]];
  const lethal = blocked.map((a) => lethalDamage(game, a, { deathtouch, marked }));
  const division = divide(game, blocker.controller, blocker, blocked, power, lethalFirst(power, lethal, -1), (choice) =>
    divisionCovers(choice, blocked.length, power),
  );
  return asAssignments(blocker, blocked, division, deathtouch, lifelink);
}

/**
 * Ask whoever divides, and hold them to the rules: an illegal division is
 * refused and the legal default stands (CR 510.1e).
 */
function divide(
  game: Game,
  chooser: PlayerId,
  source: GameObject,
  recipients: readonly Recipient[],
  total: number,
  fallback: Map<number, number>,
  legal: (choice: ReadonlyMap<number, number>) => boolean,
): ReadonlyMap<number, number> {
  const agent = agentFor(game, chooser) as CombatAgent | null | undefined;
  if (agent?.assignCombatDamage === undefined) return fallback;
  const choice = agent.assignCombatDamage(game, chooser, source.id, recipients.slice(), total);
  if (!(choice instanceof Map) || !legal(choice)) return fallback;
  return choice;
}

function asAssignments(
  source: GameObject,
  recipients: readonly Recipient[],
  division: ReadonlyMap<number, number>,
  deathtouch: boolean,
  lifelink: boolean,
): Assignment[] {
  return [...division]
    .sort(([a], [b]) => a - b)
    .filter(([, amount]) => amount > 0)
    .map(([index, amount]) => [source, recipients[index]!, amount, deathtouch, lifelink]);
}

/** What an unblocked attacker damages: a player, planeswalker or battle, or nothing. */
function damageRecipient(game: Game, combat: Combat, attacker: GameObject): Recipient | null {
  const permanentId = combat.attackingPermanent.get(attacker.id);
  if (permanentId !== undefined) {
    // CR 506.4c: what it attacked left combat; it attacks nothing.
    if (permanentId === NO_OBJECT) return null;
    const obj = game.objects.get(permanentId);
    if (obj !== undefined && isPermanent(obj)) return obj;
    // CR 508.4: the planeswalker is gone - no damage, not redirected.
    return null;
  }
  const playerId = combat.attacking.get(attacker.id);
  if (playerId === undefined || playerOf(game, playerId).hasLost) return null;
  return playerId;
}

// -- end of combat (CR 511)

/** CR 511.3: everything stops being an attacker or blocker. */
export function endCombat(game: Game): void {
  clearCombat(combatOf(game));
  emit(game, event(EventKind.REMOVED_FROM_COMBAT, { player: game.activePlayer }));
}

function combatOf(game: Game): Combat {
  let existing = game.combat as Combat | null;
  if (existing == null) {
    existing = newCombat();
    game.combat = existing;
  }
  return existing;
}

