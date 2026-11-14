/**
 * A run across several workers: the browser's replacement for the Python
 * runner's process pool.
 *
 * The games are split into contiguous index ranges, one per worker. Each
 * game's seed depends only on the run seed and its index, so the merged
 * result is the same whatever the number of workers, and the same as
 * playing them all in one.
 */

import { DEFAULT_ENGINE, mergeResults, splitRange, type EngineName, type RunResult, type RunSetup } from "@goldfly/engine/sim";
import { EngineClient } from "./client";

export interface PoolRun {
  readonly result: Promise<RunResult>;
  /** Stop every worker after its current game; `result` still resolves. */
  cancel(): void;
}

/** Workers to use by default: every core but one, for the page itself. */
export function defaultWorkers(): number {
  const cores = typeof navigator === "undefined" ? 2 : navigator.hardwareConcurrency || 2;
  return Math.max(1, cores - 1);
}

export class WorkerPool {
  private readonly clients: EngineClient[];

  constructor(size = defaultWorkers(), make: () => EngineClient = () => new EngineClient()) {
    this.clients = Array.from({ length: Math.max(1, size) }, make);
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
  ): PoolRun {
    const ranges = splitRange(games, this.clients.length);
    const done = new Array<number>(ranges.length).fill(0);
    const handles = ranges.map(([start, end], slot) => {
      const client = this.clients[slot] as EngineClient;
      const handle = client.run(setup, start, end, (finished) => {
        done[slot] = finished;
        onProgress?.(done.reduce((a, b) => a + b, 0), games);
      }, engine);
      return { client, handle };
    });
    const result = Promise.all(handles.map(({ handle }) => handle.result)).then(mergeResults);
    return {
      result,
      cancel: () => handles.forEach(({ client, handle }) => client.cancel(handle.id)),
    };
  }

  terminate(): void {
    for (const client of this.clients) client.terminate();
  }
}
