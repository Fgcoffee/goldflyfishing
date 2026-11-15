/**
 * A run across several workers: the browser's replacement for the Python
 * runner's process pool.
 *
 * The games are split into contiguous index ranges, one per worker. Each
 * game's seed depends only on the run seed and its index, so the merged
 * result is the same whatever the number of workers, and the same as
 * playing them all in one.
 *
 * A run always finishes. Workers report every game as it ends, and the page
 * keeps those games itself. So when a worker goes quiet for too long (a game
 * that never ends: the watchdog) or dies (out of memory, a stack overflow,
 * a script that won't load), nothing it finished is lost:
 * - the game it was on is recorded as crashed, with its seed, so it can be
 *   replayed in Node;
 * - a fresh worker carries on with the rest of that range.
 */

import {
  DEFAULT_ENGINE, mergeResults, seedFor, splitRange,
  type CrashedGame, type EngineName, type GameOutcome, type GameRecord, type RunResult, type RunSetup,
} from "@goldfly/engine/sim";
import { EngineClient, type RunHandle } from "./client";

export interface PoolRun {
  readonly result: Promise<RunResult>;
  /** Stop every worker after its current game; `result` still resolves. */
  cancel(): void;
}

export interface PoolOptions {
  /** A worker that reports no game for this long is stopped. Default 60 s. */
  readonly gameTimeoutMs?: number;
  /**
   * Fresh workers tried in a row without one finishing a game, before the
   * rest of a range is given up as crashed. Stops a worker that can't even
   * load from being restarted for ever.
   */
  readonly maxRestarts?: number;
  /** The engine build, written into a timed-out or dead game's detail. */
  readonly engineBuild?: string;
  /** Where to say what each worker is doing (timeouts, restarts), for debugging. */
  readonly log?: (message: string) => void;
  /** After Stop, ms before a worker that hasn't answered is terminated. */
  readonly stopGraceMs?: number;
}

export const DEFAULT_GAME_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_RESTARTS = 3;

/** Workers to use by default: every core but one, for the page itself. */
export function defaultWorkers(): number {
  const cores = typeof navigator === "undefined" ? 2 : navigator.hardwareConcurrency || 2;
  return Math.max(1, cores - 1);
}

/** How one worker's turn on a range ended. */
type SliceEnd =
  | { readonly kind: "finished" }
  | { readonly kind: "timeout" }
  | { readonly kind: "stopped" }
  | { readonly kind: "died"; readonly message: string };

/**
 * After Stop, how long a worker has to finish its game and answer before
 * it's terminated. A worker reads the cancel only between games, so one
 * stuck in a game never would.
 */
export const STOP_GRACE_MS = 3_000;

export class WorkerPool {
  private readonly clients: EngineClient[];

  constructor(
    size = defaultWorkers(),
    private readonly make: () => EngineClient = () => new EngineClient(),
  ) {
    this.clients = Array.from({ length: Math.max(1, size) }, () => make());
  }

  get size(): number {
    return this.clients.length;
  }

  /** Play `games` games of a run, reporting overall progress. */
  run(
    setup: RunSetup,
    games: number,
    onProgress?: (done: number, total: number) => void,
    engine: EngineName = DEFAULT_ENGINE,
    options: PoolOptions = {},
  ): PoolRun {
    const timeoutMs = options.gameTimeoutMs ?? DEFAULT_GAME_TIMEOUT_MS;
    const maxRestarts = options.maxRestarts ?? DEFAULT_MAX_RESTARTS;
    const log = options.log ?? (() => {});
    const detail = options.engineBuild ? `engine build ${options.engineBuild}` : "";
    const ranges = splitRange(games, this.clients.length);
    const done = new Array<number>(ranges.length).fill(0);
    const stopGraceMs = options.stopGraceMs ?? STOP_GRACE_MS;
    /** Per worker: its run, and how to end its slice at once (after Stop). */
    const current: ((RunHandle & { client: EngineClient; stopNow: () => void }) | undefined)[] = [];
    let cancelled = false;

    const report = (): void => onProgress?.(done.reduce((a, b) => a + b, 0), games);

    const playRange = async (slot: number, start: number, end: number): Promise<RunResult> => {
      const records: GameRecord[] = [];
      const crashed: CrashedGame[] = [];
      let next = start;
      let failures = 0;
      const keep = (game: GameOutcome): void => {
        if (game.record !== undefined) records.push(game.record);
        else crashed.push(game.crash);
        done[slot] = (done[slot] ?? 0) + 1;
        report();
      };

      while (next < end && !cancelled) {
        const client = this.clients[slot] as EngineClient;
        const from = next;
        let finishedHere = 0;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const ended = await new Promise<SliceEnd>((resolve) => {
          const arm = (): void => {
            clearTimeout(timer);
            timer = setTimeout(() => {
              // The game is stuck: only stopping the worker stops it.
              resolve({ kind: "timeout" });
              client.terminate();
            }, timeoutMs);
          };
          arm();
          const handle = client.run(setup, from, end, (_done, _total, game) => {
            if (game === undefined) return;
            finishedHere += 1;
            next = from + finishedHere;
            keep(game);
            arm();
          }, engine);
          current[slot] = {
            ...handle,
            client,
            stopNow: () => {
              resolve({ kind: "stopped" });
              client.terminate();
            },
          };
          handle.result.then(
            () => resolve({ kind: "finished" }),
            (error: unknown) => resolve({ kind: "died", message: error instanceof Error ? error.message : String(error) }),
          );
        });
        clearTimeout(timer);
        current[slot] = undefined;
        if (ended.kind === "finished") break;
        if (ended.kind === "stopped") {
          // Stop, and this worker didn't answer in time: it was mid-game.
          const index = next;
          log(`worker ${slot}: game ${index} (seed ${seedFor(setup.runSeed, index)}) stopped mid-game`);
          keep({ crash: { index, seed: seedFor(setup.runSeed, index), error: "stopped mid-game", detail } });
          this.clients[slot] = this.make();
          break;
        }

        // The game in flight didn't finish: record it, and carry on past it.
        const index = next;
        const error = ended.kind === "timeout"
          ? `timed out after ${Number((timeoutMs / 1000).toFixed(1))} s`
          : `worker died: ${ended.message}`;
        log(`worker ${slot}: game ${index} (seed ${seedFor(setup.runSeed, index)}) ${error}; starting a fresh worker`);
        keep({ crash: { index, seed: seedFor(setup.runSeed, index), error, detail } });
        next = index + 1;
        client.terminate();
        this.clients[slot] = this.make();

        failures = finishedHere > 0 ? 1 : failures + 1;
        if (failures > maxRestarts && next < end) {
          log(`worker ${slot}: ${failures} fresh workers in a row finished nothing; giving up games ${next} to ${end - 1}`);
          for (let i = next; i < end; i += 1) {
            keep({ crash: { index: i, seed: seedFor(setup.runSeed, i), error: "not played: the worker kept failing to start", detail } });
          }
          next = end;
        }
      }
      return { records, crashed, complete: next >= end };
    };

    const result = Promise.all(ranges.map(([start, end], slot) => playRange(slot, start, end))).then(mergeResults);
    return {
      result,
      cancel: () => {
        if (cancelled) return;
        cancelled = true;
        current.forEach((handle, slot) => {
          if (handle === undefined) return;
          handle.client.cancel(handle.id);
          // A worker stuck in a game never reads the cancel: end it.
          setTimeout(() => {
            if (current[slot] === handle) handle.stopNow();
          }, stopGraceMs);
        });
      },
    };
  }

  terminate(): void {
    for (const client of this.clients) client.terminate();
  }
}
