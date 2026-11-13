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
import { addCounters as putCounters, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { shuffle } from "../../kernel/random";
import { emit } from "../emit";
import { replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";
import { printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { Act, prohibited } from "../cr500_turn_structure/restrictions";
import { initiativeLeftTheGame, monarchLeftTheGame } from "../cr700_additional_rules/designations";
import { moveObject, removeFromZone } from "../zones";

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
