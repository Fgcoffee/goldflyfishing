/**
 * The event bus: `emit` announces that something happened. Ported from
 * `Game.emit` and its helpers in mtgfish/rules/kernel/game.py.
 *
 * Every event is logged (and hashed into the digest), passed to the
 * simulator's observer, tallied for "this turn" questions, and checked for
 * the few things the game itself reacts to (speed, Ring-bearers, CR 700
 * terms). Then triggered abilities watching it are collected; they go on the
 * stack the next time a player would receive priority (CR 603.3b), not now.
 */

import { event, EventKind, eventText, type GameEvent } from "../kernel/events";
import { logRecord, player, turnHistoryCountKey, turnHistoryKey, type Game } from "../kernel/game";
import { NO_OBJECT, NO_PLAYER } from "../kernel/ids";
import { increaseSpeed } from "./cr100_game_concepts/player";
import { GENERAL_TERM_EVENTS, note } from "./cr700_additional_rules/general";

type TriggerCollector = (game: Game, e: GameEvent) => void;

let collectTriggers: TriggerCollector = () => {};

/**
 * Where triggered abilities are collected (cr603_triggers.collect_triggers).
 * Installed by the triggers module when it is ported; until then nothing
 * triggers.
 */
export function installTriggerCollector(collector: TriggerCollector): void {
  collectTriggers = collector;
}

/** Announce that something happened. */
export function emit(game: Game, e: GameEvent): void {
  logRecord(game, eventText(e), { kind: "event", player: e.player });
  if (game.observer !== null) game.observer(game, e);
  recordThisTurn(game, e);
  if (e.kind === EventKind.LIFE_LOST) advanceSpeed(game, e);
  else if (e.kind === EventKind.CONTROL_CHANGED) endRingBearerOnControlChange(game, e);
  if (GENERAL_TERM_EVENTS.has(e.kind)) note(game, e);
  collectTriggers(game, e);
}

/** Tally an event so "did this happen this turn" can be answered. */
function recordThisTurn(game: Game, e: GameEvent): void {
  const history = game.turnHistory;
  const key = turnHistoryKey(e.kind, e.player);
  history.set(key, (history.get(key) ?? 0) + Math.max(1, Math.trunc(e.amount || 0)));
  const counted = turnHistoryCountKey(e.kind, e.player);
  history.set(counted, (history.get(counted) ?? 0) + 1);
}

/**
 * CR 701.54a: a creature stops being a player's Ring-bearer when another
 * player gains control of it.
 */
function endRingBearerOnControlChange(game: Game, e: GameEvent): void {
  for (const p of game.players) {
    if (p.ringBearer === e.objectId && e.player !== p.id) p.ringBearer = NO_OBJECT;
  }
}

/**
 * CR 702.179d: the active player, with speed 1-3, gains speed when an
 * opponent loses life during their turn - at most once a turn.
 */
function advanceSpeed(game: Game, e: GameEvent): void {
  const loser = e.player;
  if (loser === NO_PLAYER) return;
  const active = player(game, game.activePlayer);
  if (active.id === loser || active.speed === 0) return;
  if (increaseSpeed(active)) {
    emit(game, event(EventKind.SPEED_INCREASED, { player: active.id, amount: active.speed }));
  }
}
