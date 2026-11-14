/**
 * What a run produces: one summary per finished game, and the games that
 * crashed. Mirrors Python `mtgfish/sim/records.py` `GameRecord` and
 * `mtgfish/sim/runner.py` `CrashedGame`. Plain data only, because records
 * cross from a worker to the page by structured clone.
 *
 * Field names are camelCase forms of Python's.
 */

/** Python `WinReason` names, as strings so they read the same in both. */
export type WinReason =
  | "LIFE"
  | "COMMANDER_DAMAGE"
  | "POISON"
  | "DECKING"
  | "ALTERNATE"
  | "CONCEDE"
  | "STALL_OUT"
  | "DRAW";

export interface Elimination {
  readonly player: number;
  /** The engine's global turn counter when they lost. */
  readonly turn: number;
  readonly reason: string;
}

export interface CardCast {
  readonly name: string;
  /** The hero's own turn number. */
  readonly round: number;
}

/**
 * One player's position at the end of their own turn, numbered by their own
 * turns (Python `TurnSnapshot`): what a person means by "turn 5".
 */
export interface TurnSnapshot {
  readonly turn: number;
  readonly player: number;
  readonly life: number;
  readonly lands: number;
  /** Every mana source they control, tapped or not. */
  readonly manaAvailable: number;
  readonly cardsInHand: number;
  readonly permanents: number;
  readonly boardPower: number;
  readonly creatures: number;
}

/** One of the hero's objects leaving because of somebody else (Python `RemovalEvent`). */
export interface RemovalEvent {
  /** The engine's global turn counter. */
  readonly turn: number;
  readonly victim: string;
  readonly victimController: number;
  readonly source: string;
  readonly sourceController: number;
  /** "destroyed", "exiled", "sacrificed", "bounced", "countered" or "died". */
  readonly how: string;
}

export interface GameRecord {
  readonly index: number;
  readonly seed: number;
  /** The engine's global player-turn counter (see Python `turns`). */
  readonly turns: number;
  /** The hero's own turns begun: what a player means by "turn". */
  readonly rounds: number;
  /** Seat that won, or null for a stall or a draw. */
  readonly winner: number | null;
  readonly winReason: WinReason;
  readonly eliminations: readonly Elimination[];
  /** Per seat, the round its commander first resolved, or null. */
  readonly commanderLanded: readonly (number | null)[];
  readonly mulligans: readonly number[];
  /** The hero's cards: opening hand, everything drawn, everything cast. */
  readonly openingHand: readonly string[];
  readonly drawn: readonly string[];
  readonly cast: readonly CardCast[];
  /** Every player's end-of-own-turn snapshots. */
  readonly turnsSeries: readonly TurnSnapshot[];
  /** The hero's objects lost to other players. */
  readonly removal: readonly RemovalEvent[];
  /** Set when the action budget stopped the game: what was looping (a card bug). */
  readonly runaway: string;
  /** CR 104.4b: a draw on a mandatory loop, counted apart from stall-outs. */
  readonly loopDraw: boolean;
  /** The game-log digest: one seed must always give the same value. */
  readonly digest: string;
}

export interface CrashedGame {
  readonly index: number;
  readonly seed: number;
  /** "TypeError: x is undefined", for a one-line report. */
  readonly error: string;
  /** The stack, for a bug report. */
  readonly detail: string;
}
