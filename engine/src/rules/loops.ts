/**
 * Detecting repeating loops and shortcutting them (CR 732, 104.4b).
 * PORT: placeholder - mtgfish/rules/kernel/loops.py comes after resolution
 * and triggers (ROADMAP "rules"); until then the priority loop's action
 * budget is what stops a runaway.
 */

import type { Game } from "../kernel/game";
import type { PlayerId } from "../kernel/ids";
import type { Action } from "./cr100_game_concepts/priority";

export type LoopStep = unknown;

export function beginWindow(_game: Game): void {}
export function actionStep(_game: Game, _player: PlayerId, _action: Action): LoopStep | null {
  return null;
}
export function resolutionStep(_game: Game, _top: unknown): LoopStep | null {
  return null;
}
/** Whether noting this step shortcut a loop (and maybe ended the game). */
export function noteStep(_game: Game, _step: LoopStep | null): boolean {
  return false;
}
export function isExhausted(_game: Game, _player: PlayerId, _action: Action): boolean {
  return false;
}
