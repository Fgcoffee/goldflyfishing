/**
 * Game actions shared by many rules. Ported from
 * mtgfish/rules/cr100_game_concepts/actions.py, one action at a time as the
 * rules that use them are ported.
 */

import { LossReason, lossReasonName, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import {
  invalidateAllCharacteristics,
  livingPlayers,
  logRecord,
  permanents,
  player,
  type Game,
} from "../../kernel/game";
import { addCounters as putCounters, counterCount, isLive, isPermanent, objectText, removeCounters as takeCounters, type GameObject } from "../../kernel/gameobject";
import { allocateTimestamp, NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { shuffle } from "../../kernel/random";
import { emit } from "../emit";
import { replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";
import { printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { Act, prohibited, restrictionText } from "../cr500_turn_structure/restrictions";
import { CardType } from "../../kernel/enums";
import { gainLife as gainLifeRaw, loseLife as loseLifeRaw, takeCommanderDamage } from "./player";
import { lifeChangeProhibited } from "../cr600_spells_and_abilities/cost_checks";
import { playerProtectedFrom, protectedFrom } from "./protection";
import { recordCombatDamageSource } from "../cr700_additional_rules/combat_damage_record";
import { commanderIdentity } from "../zones";
import { combatRemove, type Combat } from "../cr500_turn_structure/combat";
import { hasKeyword, hasType, keywordAbilities, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { initiativeLeftTheGame, monarchLeftTheGame } from "../cr700_additional_rules/designations";
import { moveObject, removeFromZone } from "../zones";
import { num } from "../ir_read";

/**
 * Put counters on an object, through replacement effects (CR 122.6, 614.1a).
 * CR 107.1b: a calculated amount below zero puts none on, it doesn't remove.
 * Returns how many were added.
 */
export function addCounters(
  game: Game,
  obj: GameObject,
  kind: string,
  amount: number,
  options: { source?: ObjectId } = {},
): number {
  if (amount <= 0) return 0;
  const source = options.source ?? NO_OBJECT;
  let count = amount;
  const hooks = replacementHooks();
  if (hooks.hasReplacements(game)) {
    const prospective = hooks.replace(
      game,
      event(EventKind.COUNTER_ADDED, { objectId: obj.id, player: obj.controller, source, amount: count, data: [kind] }),
    );
    if (prospective === null) return 0;
    count = prospective.amount;
    if (count <= 0) return 0;
  }
  const added = putCounters(obj, kind, count);
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.COUNTER_ADDED, { objectId: obj.id, player: obj.controller, source, amount: added, data: [kind] }));
  return added;
}

/** CR 701.24: shuffle a library with the game's seeded PRNG. */
export function shuffleLibrary(game: Game, playerId: PlayerId): void {
  shuffle(game.rng, player(game, playerId).library);
  emit(game, event(EventKind.SHUFFLED, { player: playerId }));
}

/**
 * Draw cards (CR 121). An empty library sets a flag; the loss is a
 * state-based action (CR 704.5b) - unless the bench switched that off.
 */
export function draw(game: Game, playerId: PlayerId, count = 1): GameObject[] {
  const p = player(game, playerId);
  const drawn: GameObject[] = [];
  if (count > 0 && prohibited(game, Act.DRAW_CARD, { player: playerId }) !== null) return drawn;
  for (let i = 0; i < count; i += 1) {
    const top = p.library[0];
    if (top === undefined) {
      if (!game.relaxations.drawsFromAnEmptyLibraryDoNothing) p.attemptedDrawFromEmptyLibrary = true;
      break;
    }
    const card = moveObject(game, game.objects.get(top) as GameObject, Zone.HAND, { toPlayer: playerId });
    p.cardsDrawnThisTurn += 1;
    drawn.push(card);
    emit(game, event(EventKind.DREW_CARD, { objectId: card.id, player: playerId, amount: 1 }));
  }
  return drawn;
}

/** CR 701.9: discard a card. */
export function discard(game: Game, obj: GameObject, options: { source?: ObjectId } = {}): GameObject {
  emit(game, event(EventKind.DISCARDED, { objectId: obj.id, player: obj.owner, source: options.source ?? NO_OBJECT }));
  return moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
}

/** CR 701.17: mill cards. Returns how many. */
export function mill(game: Game, playerId: PlayerId, count: number, options: { source?: ObjectId } = {}): number {
  const p = player(game, playerId);
  let milled = 0;
  for (let i = 0; i < count; i += 1) {
    const top = p.library[0];
    if (top === undefined) break;
    moveObject(game, game.objects.get(top) as GameObject, Zone.GRAVEYARD, { toPlayer: playerId });
    milled += 1;
  }
  if (milled > 0) emit(game, event(EventKind.MILLED, { player: playerId, amount: milled, source: options.source ?? NO_OBJECT }));
  return milled;
}

/**
 * A player leaves the game (CR 104.3, 800.4): everything they own leaves
 * with them, and control effects giving them others' permanents end.
 */
export function playerLoses(game: Game, playerId: PlayerId, reason: LossReason): void {
  const p = player(game, playerId);
  if (p.hasLost) return;
  if (p.cannotLose && reason !== LossReason.CONCEDE) return;
  p.hasLost = true;
  p.lossReason = reason;
  p.leftOnTurn = game.turn;
  logRecord(game, `${p.name || `P${playerId}`} loses (${lossReasonName(reason)})`, { kind: "loss", player: playerId });
  revealAll(game, playerId);
  for (const [id, obj] of [...game.objects]) {
    if (obj.owner !== playerId) continue;
    removeFromZone(game, obj);
    game.objects.delete(id);
  }
  for (const obj of permanents(game)) {
    if (obj.controller === playerId && obj.owner !== playerId) {
      obj.controller = obj.owner;
      obj.baseController = obj.owner;
      obj.summoningSick = true;
      obj.newlyControlled = true;
    }
  }
  invalidateAllCharacteristics(game);
  if (p.isMonarch) {
    p.isMonarch = false;
    monarchLeftTheGame(game);
  }
  if (p.hasInitiative) {
    p.hasInitiative = false;
    initiativeLeftTheGame(game);
  }
  emit(game, event(EventKind.PLAYER_LEFT_GAME, { player: playerId }));
  checkGameOver(game);
}

/** CR 104.3a: a player may concede at any time; control never transfers it (CR 723.6). */
export function concede(game: Game, playerId: PlayerId): boolean {
  playerLoses(game, playerId, LossReason.CONCEDE);
  return true;
}

/** CR 104.2b: a spell or ability says a player wins; everyone else loses. */
export function playerWins(game: Game, playerId: PlayerId): void {
  const p = player(game, playerId);
  if (p.cannotWin) return;
  p.hasWon = true;
  for (const other of game.players) if (other.id !== playerId) playerLoses(game, other.id, LossReason.EFFECT);
  game.gameOver = true;
  game.winners = [playerId];
}

export function checkGameOver(game: Game): void {
  const living = livingPlayers(game);
  if (living.length <= 1) {
    game.gameOver = true;
    game.winners = living.map((p) => p.id);
    revealAll(game);
  }
}

/** CR 708.9: face-down objects are revealed when the game ends or their owner leaves. */
function revealAll(game: Game, owner?: PlayerId): void {
  for (const id of [...game.battlefield, ...game.stack]) {
    const obj = game.objects.get(id);
    if (obj === undefined || !obj.faceDown) continue;
    if (owner !== undefined && obj.owner !== owner) continue;
    const name = obj.card !== null ? printedCharacteristics(game, obj).name : "";
    const why = owner === undefined ? "the game ended" : "its owner left";
    logRecord(game, `face-down #${obj.id} is revealed: ${name} (${why})`, { kind: "reveal", player: obj.owner });
  }
}

/** Tap a permanent (CR 701.26a). Returns whether it became tapped. */
export function tap(game: Game, obj: GameObject, options: { source?: ObjectId } = {}): boolean {
  if (obj.supersededBy !== NO_OBJECT || obj.zone !== Zone.BATTLEFIELD || obj.tapped) return false; // CR 110.1
  obj.tapped = true;
  emit(game, event(EventKind.TAPPED, { objectId: obj.id, player: obj.controller, source: options.source ?? NO_OBJECT }));
  return true;
}

/** Untap a permanent (CR 701.26b), unless something says it can't untap. */
export function untap(game: Game, obj: GameObject, options: { source?: ObjectId } = {}): boolean {
  if (obj.supersededBy !== NO_OBJECT || obj.zone !== Zone.BATTLEFIELD || !obj.tapped) return false;
  if (prohibited(game, Act.UNTAP, { obj }) !== null) return false;
  obj.tapped = false;
  emit(game, event(EventKind.UNTAPPED, { objectId: obj.id, player: obj.controller, source: options.source ?? NO_OBJECT }));
  return true;
}

/** Sacrifice a permanent (CR 701.17): not destroying, so indestructible doesn't stop it. */
export function sacrifice(game: Game, obj: GameObject, options: { source?: ObjectId } = {}): boolean {
  if (!isPermanent(obj)) return false; // CR 110.1
  emit(game, event(EventKind.SACRIFICED, { objectId: obj.id, player: obj.controller, source: options.source ?? NO_OBJECT }));
  moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
  return true;
}

/** CR 607.2: remember that this ability moved this object. */
export function recordLink(game: Game, source: ObjectId, objectId: ObjectId, linkId = 0): void {
  const entries = game.exiledWith.get(source) ?? [];
  entries.push([objectId, linkId]);
  game.exiledWith.set(source, entries);
}

/** CR 607.2: what the other half of a linked ability refers to, still in place. */
export function linkedObjects(game: Game, source: ObjectId, linkId = 0): GameObject[] {
  const out: GameObject[] = [];
  for (const [id, entryLink] of game.exiledWith.get(source) ?? []) {
    if (linkId && entryLink !== linkId) continue; // CR 607.2a
    const obj = game.objects.get(id);
    if (obj !== undefined && isLive(obj)) out.push(obj);
  }
  return out;
}

/** Exile an object (CR 701.6, 406.2), linking it to what exiled it (CR 607.2); face down per CR 406.3. */
export function exile(
  game: Game,
  obj: GameObject,
  options: { source?: ObjectId; linkId?: number; faceDown?: boolean } = {},
): GameObject {
  const source = options.source ?? NO_OBJECT;
  emit(game, event(EventKind.EXILED, { objectId: obj.id, source }));
  const exiled = moveObject(game, obj, Zone.EXILE, options.faceDown ? { faceDown: "" } : {});
  // CR 607.2a: only a card that really arrived is linked.
  if (source !== NO_OBJECT && exiled !== obj) recordLink(game, source, exiled.id, options.linkId ?? 0);
  return exiled;
}

/** Return an object to its owner's hand. */
export function bounce(game: Game, obj: GameObject, options: { source?: ObjectId } = {}): GameObject {
  emit(game, event(EventKind.RETURNED_TO_HAND, { objectId: obj.id, source: options.source ?? NO_OBJECT }));
  return moveObject(game, obj, Zone.HAND, { toPlayer: obj.owner });
}

/** Move to the graveyard without destroying (CR 701.7's "put into"). */
export function putIntoGraveyard(game: Game, obj: GameObject): GameObject {
  return moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
}

/** Unattach (CR 701.3b). */
export function detach(game: Game, attachment: GameObject): boolean {
  if (attachment.attachedPlayer !== NO_PLAYER) {
    attachment.attachedPlayer = NO_PLAYER;
    invalidateAllCharacteristics(game);
    emit(game, event(EventKind.UNATTACHED, { objectId: attachment.id }));
    return true;
  }
  if (attachment.attachedTo === NO_OBJECT) return false;
  const host = game.objects.get(attachment.attachedTo);
  if (host !== undefined) {
    const at = host.attachments.indexOf(attachment.id);
    if (at >= 0) host.attachments.splice(at, 1);
  }
  attachment.attachedTo = NO_OBJECT;
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.UNATTACHED, { objectId: attachment.id }));
  return true;
}

/** Remove counters from an object; how many came off. */
export function removeCounters(game: Game, obj: GameObject, kind: string, amount: number, options: { source?: ObjectId } = {}): number {
  const removed = takeCounters(obj, kind, amount);
  if (removed) {
    invalidateAllCharacteristics(game);
    emit(game, event(EventKind.COUNTER_REMOVED, {
      objectId: obj.id, player: obj.controller, source: options.source ?? NO_OBJECT, amount: removed, data: [kind],
    }));
  }
  return removed;
}

/** CR 702.12b: indestructible permanents ignore destruction and lethal damage. */
export function isIndestructible(game: Game, obj: GameObject): boolean {
  return hasKeyword(characteristicsOf(game, obj), "Indestructible");
}

/**
 * CR 506.4: remove a permanent from combat - attacking, blocking, blocked,
 * or being attacked - and recompute the board ("attacking creatures get
 * +1/+0" stops applying at once).
 */
export function removeFromCombat(game: Game, obj: GameObject): void {
  const combat = game.combat as Combat | null;
  if (combat == null) return;
  if (
    !(
      combat.attacking.has(obj.id) ||
      combat.blocking.has(obj.id) ||
      [...combat.attackingPermanent.values()].includes(obj.id) ||
      combat.wasBlocked.has(obj.id)
    )
  ) {
    return;
  }
  combatRemove(combat, obj.id);
  logRecord(game, `${objectText(obj, characteristicsOf(game, obj).name)} is removed from combat`, { kind: "combat" });
  invalidateAllCharacteristics(game);
}

/**
 * Destroy a permanent (CR 701.7); whether it went to the graveyard.
 * Indestructible (CR 702.12b), "can't be destroyed" (CR 101.2) and a
 * regeneration shield (CR 701.15) stop it; `regenerate: false` for "can't be
 * regenerated", which leaves the shield unused (CR 701.19c).
 */
export function destroy(game: Game, obj: GameObject, options: { source?: ObjectId; regenerate?: boolean } = {}): boolean {
  const source = options.source ?? NO_OBJECT;
  let regenerate = options.regenerate ?? true;
  if (!isPermanent(obj)) return false; // CR 110.1: only the live permanent
  const text = () => objectText(obj, characteristicsOf(game, obj).name);
  if (isIndestructible(game, obj)) {
    logRecord(game, `${text()} is indestructible; not destroyed`, { kind: "sba" });
    return false;
  }
  const blocked = prohibited(game, Act.BE_DESTROYED, { obj });
  if (blocked !== null) {
    logRecord(game, `${text()} can't be destroyed (${restrictionText(blocked)})`, { kind: "restriction" });
    return false;
  }
  if (regenerate && prohibited(game, Act.BE_REGENERATED, { obj }) !== null) regenerate = false;
  if (regenerate && obj.regenerationShields > 0) {
    obj.regenerationShields -= 1;
    obj.tapped = true;
    obj.damage = 0;
    obj.dealtDeathtouchDamage = false;
    removeFromCombat(game, obj);
    emit(game, event(EventKind.REGENERATED, { objectId: obj.id, source }));
    return false;
  }
  emit(game, event(EventKind.DESTROYED, { objectId: obj.id, source }));
  moveObject(game, obj, Zone.GRAVEYARD, { toPlayer: obj.owner });
  return true;
}

/** Attach an Aura, Equipment or Fortification (CR 701.3); a new timestamp (CR 613.7d). */
export function attach(game: Game, attachment: GameObject, host: GameObject): boolean {
  if (attachment.attachedTo === host.id) return false;
  detach(game, attachment);
  attachment.attachedTo = host.id;
  host.attachments.push(attachment.id);
  attachment.timestamp = allocateTimestamp(game.ids);
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.ATTACHED, { objectId: attachment.id, source: host.id }));
  return true;
}

/** CR 303.4b: an Aura that enchants a player is attached to that player. */
export function attachToPlayer(game: Game, attachment: GameObject, playerId: PlayerId): boolean {
  if (attachment.attachedPlayer === playerId) return false;
  detach(game, attachment);
  attachment.attachedPlayer = playerId;
  attachment.timestamp = allocateTimestamp(game.ids);
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.ATTACHED, { objectId: attachment.id, player: playerId }));
  return true;
}

/** CR 607.2q, 400.7d: the spell's links pass to the permanent it became. */
export function carryLinks(game: Game, oldId: ObjectId, newId: ObjectId): void {
  const recorded = game.exiledWith.get(oldId);
  if (recorded !== undefined && recorded.length > 0) {
    const entries = game.exiledWith.get(newId) ?? [];
    entries.push(...recorded);
    game.exiledWith.set(newId, entries);
  }
}

// -- damage and life (CR 119, 120) -----------------------------------------------------

/** How much of `amount` would be excess damage (CR 120.10), measured before it is dealt. */
export function excessDamage(game: Game, target: GameObject, amount: number): number {
  if (amount <= 0) return 0;
  const chars = characteristicsOf(game, target);
  const capacities: number[] = [];
  if (hasType(chars, CardType.CREATURE)) capacities.push(Math.max(0, (chars.toughness ?? 0) - target.damage));
  if (hasType(chars, CardType.PLANESWALKER)) capacities.push(counterCount(target, "loyalty"));
  if (hasType(chars, CardType.BATTLE)) capacities.push(counterCount(target, "defense"));
  if (capacities.length === 0) return 0;
  return Math.max(0, amount - Math.max(...capacities));
}

export interface DamageOptions {
  source?: ObjectId;
  sourceController?: PlayerId;
  deathtouch?: boolean;
  lifelink?: boolean;
  combat?: boolean;
  isCommanderSource?: boolean;
}

/**
 * Deal damage (CR 120) to a permanent or a player (a number). Nothing is
 * destroyed here: lethal damage kills through the SBAs (CR 704.5g). Zero or
 * less deals none (CR 120.8, 107.1b). Returns how much was dealt.
 */
export function dealDamage(game: Game, target: GameObject | PlayerId, amountIn: number, options: DamageOptions = {}): number {
  let amount = amountIn;
  const source = options.source ?? NO_OBJECT;
  const sourceController = options.sourceController ?? NO_PLAYER;
  const combat = options.combat ?? false;
  const lifelink = options.lifelink ?? false;
  const kind = combat ? EventKind.COMBAT_DAMAGE_DEALT : EventKind.DAMAGE_DEALT;
  if (amount <= 0) return 0;
  const toPlayer = typeof target === "number";
  // CR 614/615: prevention and modification apply before it is dealt.
  const hooks = replacementHooks();
  if (hooks.hasReplacements(game)) {
    const prospective = hooks.replace(game, event(kind, {
      objectId: toPlayer ? NO_OBJECT : (target as GameObject).id,
      player: toPlayer ? (target as PlayerId) : NO_PLAYER,
      source, sourceController, amount,
    }));
    if (prospective === null) return 0;
    amount = prospective.amount;
    if (amount <= 0) return 0;
  }
  const sourceObj = game.objects.get(source) ?? null;
  const sourceChars = sourceObj !== null ? characteristicsOf(game, sourceObj) : null;
  // CR 702.16e.
  if (!toPlayer && protectedFrom(game, target as GameObject, sourceObj)) {
    logRecord(game, `${objectText(target as GameObject, characteristicsOf(game, target as GameObject).name)} has protection; damage prevented`, { kind: "protection" });
    return 0;
  }
  if (toPlayer) {
    return damagePlayer(game, target as PlayerId, amount, { source, sourceController, lifelink, combat, isCommanderSource: options.isCommanderSource ?? false });
  }
  const obj = target as GameObject;
  const chars = characteristicsOf(game, obj);
  // CR 702.90b, 702.75a: wither and infect deal damage to creatures as -1/-1 counters.
  if (sourceChars !== null && hasType(chars, CardType.CREATURE) && (hasKeyword(sourceChars, "Wither") || hasKeyword(sourceChars, "Infect"))) {
    const excess = excessDamage(game, obj, amount);
    addCounters(game, obj, "-1/-1", amount, { source });
    emit(game, event(kind, { objectId: obj.id, player: obj.controller, source, sourceController, amount, data: [excess] }));
    if (lifelink && sourceController !== NO_PLAYER) gainLife(game, sourceController, amount, { source });
    return amount;
  }
  const excess = excessDamage(game, obj, amount); // CR 120.10
  if (hasType(chars, CardType.PLANESWALKER)) {
    removeCounters(game, obj, "loyalty", amount, { source });
  } else if (hasType(chars, CardType.BATTLE)) {
    removeCounters(game, obj, "defense", amount, { source });
  } else if (hasType(chars, CardType.CREATURE)) {
    obj.damage += amount;
    if (options.deathtouch) obj.dealtDeathtouchDamage = true;
  } else {
    return 0; // CR 120.3
  }
  emit(game, event(kind, { objectId: obj.id, player: obj.controller, source, sourceController, amount, data: [excess] }));
  if (lifelink && sourceController !== NO_PLAYER) gainLife(game, sourceController, amount, { source });
  return amount;
}

/** Damage to a player causes that much life loss (CR 120.3a), or poison with infect (CR 702.90c). */
function damagePlayer(
  game: Game,
  playerId: PlayerId,
  amount: number,
  o: { source: ObjectId; sourceController: PlayerId; lifelink: boolean; combat: boolean; isCommanderSource: boolean },
): number {
  const p = player(game, playerId);
  const kind = o.combat ? EventKind.COMBAT_DAMAGE_DEALT : EventKind.DAMAGE_DEALT;
  if (playerProtectedFrom(game, playerId, game.objects.get(o.source) ?? null)) {
    logRecord(game, `${p.name} has protection; damage prevented`, { kind: "protection" }); // CR 702.16e
    return 0;
  }
  if (o.combat && p.hasLost) return 0; // CR 800.4e
  if (o.combat) recordCombatDamageSource(game, o.source, o.sourceController); // CR 702.76a, 702.173a
  const sourceObj = game.objects.get(o.source);
  const sourceChars = sourceObj !== undefined ? characteristicsOf(game, sourceObj) : null;
  if (sourceChars !== null && hasKeyword(sourceChars, "Infect")) {
    addPoison(game, playerId, amount);
    emit(game, event(kind, { player: playerId, source: o.source, sourceController: o.sourceController, amount }));
    if (o.lifelink && o.sourceController !== NO_PLAYER) gainLife(game, o.sourceController, amount, { source: o.source });
    if (o.combat) toxicPoison(game, playerId, sourceChars);
    return amount;
  }
  const lifeLost = replacedLifeLoss(game, playerId, amount, { source: o.source });
  loseLifeRaw(p, lifeLost);
  // CR 903.10: combat damage from a commander is tracked; 21 from one is lethal.
  if (o.combat && o.isCommanderSource && o.source !== NO_OBJECT) {
    const total = takeCommanderDamage(p, commanderIdentity(game, o.source), amount);
    emit(game, event(EventKind.COMMANDER_DAMAGE_DEALT, { player: playerId, source: o.source, sourceController: o.sourceController, amount: total }));
  }
  emit(game, event(kind, { player: playerId, source: o.source, sourceController: o.sourceController, amount }));
  if (lifeLost > 0) emit(game, event(EventKind.LIFE_LOST, { player: playerId, amount: lifeLost, source: o.source }));
  if (o.lifelink && o.sourceController !== NO_PLAYER) gainLife(game, o.sourceController, amount, { source: o.source });
  if (o.combat) toxicPoison(game, playerId, sourceChars);
  return amount;
}

/** CR 120.3g, 702.164c: toxic's poison on combat damage, the total toxic value. */
function toxicPoison(game: Game, playerId: PlayerId, sourceChars: Characteristics | null): void {
  if (sourceChars === null) return;
  const total = keywordAbilities(sourceChars, "Toxic").reduce((t, a) => t + num((a.quality as { constant?: number } | null)?.constant ?? 0), 0);
  if (total > 0) addPoison(game, playerId, total);
}

/** Gain life (CR 119.3); replaceable, and zero is no event (CR 119.9-10). */
export function gainLife(game: Game, playerId: PlayerId, amountIn: number, options: { source?: ObjectId } = {}): number {
  let amount = amountIn;
  const source = options.source ?? NO_OBJECT;
  if (amount <= 0) return 0;
  if (lifeChangeProhibited(game, playerId, true)) return 0; // CR 119.7
  const hooks = replacementHooks();
  if (hooks.hasReplacements(game)) {
    const prospective = hooks.replace(game, event(EventKind.LIFE_GAINED, { player: playerId, amount, source }));
    if (prospective === null) return 0;
    if (prospective.kind === EventKind.LIFE_LOST) {
      loseLife(game, playerId, prospective.amount, { source });
      return 0;
    }
    amount = prospective.amount;
    if (amount <= 0) return 0;
  }
  const gained = gainLifeRaw(player(game, playerId), amount);
  if (gained) emit(game, event(EventKind.LIFE_GAINED, { player: playerId, amount: gained, source }));
  return gained;
}

/** How much life a player actually loses, after replacement (CR 614, 120.3a). */
export function replacedLifeLoss(game: Game, playerId: PlayerId, amount: number, options: { source?: ObjectId } = {}): number {
  if (amount > 0 && lifeChangeProhibited(game, playerId, false)) return 0;
  const hooks = replacementHooks();
  if (amount <= 0 || !hooks.hasReplacements(game)) return amount;
  const prospective = hooks.replace(game, event(EventKind.LIFE_LOST, { player: playerId, amount, source: options.source ?? NO_OBJECT }));
  return prospective === null ? 0 : prospective.amount;
}

/** Lose life, after replacements (CR 119.3). */
export function loseLife(game: Game, playerId: PlayerId, amount: number, options: { source?: ObjectId } = {}): number {
  const lost = loseLifeRaw(player(game, playerId), replacedLifeLoss(game, playerId, amount, options));
  if (lost) emit(game, event(EventKind.LIFE_LOST, { player: playerId, amount: lost, source: options.source ?? NO_OBJECT }));
  return lost;
}

/** Give a player poison counters (CR 122.1, 104.3c); never negative (CR 107.1b). */
export function addPoison(game: Game, playerId: PlayerId, amount: number): number {
  if (amount <= 0) return 0;
  player(game, playerId).poison += amount;
  emit(game, event(EventKind.POISON_ADDED, { player: playerId, amount }));
  return amount;
}
