/**
 * The engine, for the website's Results and Replay screens
 * (mtgfish/ui/web/browser_runs.js). Built on its own (`npm run build:site`)
 * into mtgfish/ui/web/engine/, which the Python server serves as static
 * files beside the page.
 *
 * A browser run comes back from GET /api/browser-runs/{id} with its records
 * and the setup to replay it. The report is built here from the records; a
 * replay plays the game again in a worker, since one game takes seconds.
 * Both answer in the JSON Python's bridge sends, so the screens can't tell
 * a browser run from a server run.
 */

import { buildReport, type RunSetup } from "@goldfly/engine/sim";
import { reportPayload, unparsedCards } from "../results";
import type { CrashedGame, GameRecord } from "@goldfly/engine/sim";
import type { ReplayReply, ReplayRequest } from "./replay_protocol";

/** Running a simulation in the browser, for the Simulate button (behind a flag). */
export { BrowserRunner, type BrowserRunOptions, type BrowserRunOutcome } from "./run";

/** Omit, per member of a union. */
type WithoutId<T> = T extends unknown ? Omit<T, "id"> : never;

/** What GET /api/browser-runs/{id} answers. */
export interface StoredRun {
  readonly run: {
    readonly id: string;
    readonly hero: number;
    readonly game_start: number;
    readonly game_end: number;
    /** What a report from this run names (the server's run row has both). */
    readonly engine_build?: string;
    readonly ir_build?: string;
  };
  readonly records: readonly GameRecord[];
  readonly crashed: readonly CrashedGame[];
  readonly setup: RunSetup | null;
  readonly unverifiable: string;
}

/** The Results screen's report for a stored run (Python `Bridge.report`). */
export function resultsFor(stored: StoredRun): Record<string, unknown> {
  const hero = stored.run.hero;
  const seats = stored.setup?.decks.length ?? 4;
  const report = buildReport(stored.records, stored.crashed, { hero, seats });
  const heroDeck = stored.setup?.decks[hero];
  const unparsed = stored.setup !== null && heroDeck !== undefined
    ? unparsedCards(heroDeck, new Map(Object.entries(stored.setup.cards)))
    : [];
  return reportPayload(report, { unparsedCards: unparsed });
}

/** The seed a game of the run was played from, or null if the run doesn't hold that game. */
export function seedOf(stored: StoredRun, index: number): number | null {
  const game = [...stored.records, ...stored.crashed].find((g) => g.index === index);
  return game === undefined ? null : game.seed;
}

/**
 * Replays a stored run's games in a worker (Python `Bridge.replay` and
 * `replay_board`). One game's film is kept in the worker; boards are asked
 * for one at a time, as the board view already does.
 */
export class Replayer {
  private readonly worker: Worker;
  private next = 1;
  private readonly waiting = new Map<number, (reply: ReplayReply) => void>();
  /** Set once the worker has failed: every later request gets it at once rather than waiting forever. */
  private failed: string | null = null;

  constructor(private readonly stored: StoredRun) {
    this.worker = new Worker(new URL("./replay.worker.ts", import.meta.url), { type: "module" });
    this.worker.addEventListener("message", (event: MessageEvent<ReplayReply>) => {
      const done = this.waiting.get(event.data.id);
      this.waiting.delete(event.data.id);
      done?.(event.data);
    });
    this.worker.addEventListener("error", (event) => {
      // A worker script that can't load fails before any request is waiting.
      this.failed = `the replay worker failed${event.message ? `: ${event.message}` : " to load"}`;
      for (const done of this.waiting.values()) done({ id: -1, payload: { error: this.failed } });
      this.waiting.clear();
    });
  }

  private ask(request: WithoutId<ReplayRequest>): Promise<Record<string, unknown>> {
    if (this.failed !== null) return Promise.resolve({ error: this.failed });
    const id = this.next++;
    return new Promise((resolve) => {
      this.waiting.set(id, (reply) => resolve(reply.payload));
      this.worker.postMessage({ ...request, id } as ReplayRequest);
    });
  }

  /** Python `Bridge.replay(index)`: the game played again, with its log and film. */
  replay(index: number): Promise<Record<string, unknown>> {
    const setup = this.stored.setup;
    if (setup === null) return Promise.resolve({ error: `this run can't be replayed: ${this.stored.unverifiable}` });
    const seed = seedOf(this.stored, index);
    if (seed === null) {
      const { game_start: start, game_end: end } = this.stored.run;
      return Promise.resolve({ error: `this run holds games ${start} to ${end - 1}; game ${index} isn't one of them` });
    }
    return this.ask({ type: "replay", setup, index, seed, hero: this.stored.run.hero }).then((payload) => {
      const recorded = this.stored.records.find((g) => g.index === index);
      // The replay must be the game the run recorded; say so loudly if not.
      if (recorded !== undefined && payload.digest !== undefined && payload.digest !== recorded.digest) {
        return { ...payload, mismatch: `replayed digest ${String(payload.digest)} differs from the run's ${recorded.digest}` };
      }
      return payload;
    });
  }

  /** Python `Bridge.replay_board(index)`. */
  board(index: number): Promise<Record<string, unknown>> {
    return this.ask({ type: "board", index });
  }

  terminate(): void {
    this.worker.terminate();
  }
}
