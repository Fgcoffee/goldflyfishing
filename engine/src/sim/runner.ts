/**
 * Playing a range of games: the TS counterpart of Python
 * `mtgfish/sim/runner.py`, without the process pool. Parallelism is the
 * page's job: it starts several workers and gives each a slice of the index
 * range. Because every game's seed comes from `seedFor(runSeed, index)`, how
 * the range is split never changes a game.
 */

import type { CardDocument } from "../ir/types";
import { seedFor } from "../kernel/random";
import type { CrashedGame, GameRecord } from "./records";

/** One seat's deck, by oracle id, as `/api/ir/decks` returns it. */
export interface DeckSpec {
  readonly commanders: readonly string[];
  readonly cards: readonly { readonly oracle_id: string; readonly count: number }[];
}

/** Everything a game needs. Plain data: it is posted to every worker. */
export interface RunSetup {
  /** Seat 0 is the deck being measured. */
  readonly decks: readonly DeckSpec[];
  /** The IR of every card in the decks, by oracle id. */
  readonly cards: Readonly<Record<string, CardDocument>>;
  readonly runSeed: number;
  /** Turn cap, per player (Python `max_rounds`). */
  readonly maxRounds: number;
}

/** A rules engine, as the runner sees it. */
export interface GameEngine {
  readonly name: string;
  play(setup: RunSetup, index: number, seed: number): GameRecord;
}

export interface RunResult {
  readonly records: GameRecord[];
  readonly crashed: CrashedGame[];
  /** False when it stopped early because `shouldStop` said so. */
  readonly complete: boolean;
}

export interface RunHooks {
  /** After each game, crashed or not. */
  onGame?(done: number, total: number): void;
  /** Checked between games. */
  shouldStop?(): boolean;
  /** Called every `yieldEvery` games so a worker can read its messages. */
  pause?(): Promise<void>;
  readonly yieldEvery?: number;
}

/**
 * Play games `start` to `end - 1`. A game that throws is left out and
 * reported, like Python's #35: one bad game never loses the rest of a run.
 */
export async function runGames(
  engine: GameEngine,
  setup: RunSetup,
  start: number,
  end: number,
  hooks: RunHooks = {},
): Promise<RunResult> {
  const records: GameRecord[] = [];
  const crashed: CrashedGame[] = [];
  const total = Math.max(0, end - start);
  const yieldEvery = Math.max(1, hooks.yieldEvery ?? 1);

  for (let index = start; index < end; index += 1) {
    if (hooks.shouldStop?.()) return { records, crashed, complete: false };
    const seed = seedFor(setup.runSeed, index);
    try {
      records.push(engine.play(setup, index, seed));
    } catch (error) {
      crashed.push(crashedGame(index, seed, error));
    }
    const done = index - start + 1;
    hooks.onGame?.(done, total);
    if (hooks.pause !== undefined && done % yieldEvery === 0 && index + 1 < end) {
      await hooks.pause();
    }
  }
  return { records, crashed, complete: true };
}

export function crashedGame(index: number, seed: number, error: unknown): CrashedGame {
  if (error instanceof Error) {
    return { index, seed, error: `${error.name}: ${error.message}`, detail: error.stack ?? "" };
  }
  return { index, seed, error: String(error), detail: "" };
}

/**
 * Split `games` into at most `parts` contiguous ranges of near-equal size,
 * as [start, end) pairs. Empty ranges are left out.
 */
export function splitRange(games: number, parts: number): [number, number][] {
  const count = Math.max(1, Math.min(Math.floor(parts), games));
  const ranges: [number, number][] = [];
  for (let i = 0; i < count; i += 1) {
    const start = Math.floor((games * i) / count);
    const end = Math.floor((games * (i + 1)) / count);
    if (end > start) ranges.push([start, end]);
  }
  return ranges;
}

/** Merge workers' results into index order. */
export function mergeResults(parts: readonly RunResult[]): RunResult {
  const records = parts.flatMap((p) => p.records).sort((a, b) => a.index - b.index);
  const crashed = parts.flatMap((p) => p.crashed).sort((a, b) => a.index - b.index);
  return { records, crashed, complete: parts.every((p) => p.complete) };
}
