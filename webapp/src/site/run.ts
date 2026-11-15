/**
 * Running a simulation in the browser, for the website's Simulate button
 * (mtgfish/ui/web/browser_runs.js, behind the browser-engine flag).
 *
 * Decklists in. Out come the Results payload (the JSON a server run sends)
 * and a stored run the Replay screen can open. In between:
 * 1. the decks' card IR is fetched (/api/ir/decks);
 * 2. the games are played on a pool of Web Workers;
 * 3. the run is uploaded (/api/browser-runs);
 * 4. the report is built.
 */

import { buildReport, DEFAULT_ENGINE, type RunResult, type RunSetup } from "@goldfly/engine/sim";
import { IrClient, type DeckManifest } from "../ir";
import { DEFAULT_GAME_TIMEOUT_MS, WorkerPool, defaultWorkers } from "../pool";
import { reportPayload, unparsedCards } from "../results";
import { ENGINE_BUILD, UploadRefused, runUpload, uploadRun } from "../upload";
import type { StoredRun } from "./entry";

export interface BrowserRunOptions {
  readonly games: number;
  readonly seed: number;
  /** A game still running after this long is stopped and recorded. Default 60. */
  readonly gameTimeoutSeconds?: number;
  /** Turn cap per player; the server's default is 25. */
  readonly maxRounds?: number;
  readonly onProgress?: (done: number, total: number) => void;
  /** Said while the cards load and the run uploads. */
  readonly onStatus?: (text: string) => void;
  /** The hero deck's name, if it has one (an Archidekt deck's), for History. */
  readonly deckName?: string;
}

export interface BrowserRunOutcome {
  /** The Results screen's report, or `{cancelled: true}` / `{error}`. */
  readonly payload: Record<string, unknown>;
  /** For the Replay screen; null when nothing was played. */
  readonly stored: StoredRun | null;
  /** The server's id for the uploaded run, or null if it wasn't stored. */
  readonly runId: string | null;
  /** Why the upload failed, if it did; the results still stand. */
  readonly uploadError: string | null;
  /** The upload was refused because a newer engine is deployed. */
  readonly reload: boolean;
}

const irClient = new IrClient();

/** One run at a time; the Stop button cancels it. */
export class BrowserRunner {
  private pool: WorkerPool | null = null;
  private cancelRun: (() => void) | null = null;

  get running(): boolean {
    return this.cancelRun !== null;
  }

  cancel(): boolean {
    if (this.cancelRun === null) return false;
    this.cancelRun();
    return true;
  }

  async run(decklists: readonly string[], options: BrowserRunOptions): Promise<BrowserRunOutcome> {
    const empty = (payload: Record<string, unknown>): BrowserRunOutcome =>
      ({ payload, stored: null, runId: null, uploadError: null, reload: false });
    if (this.running) return empty({ error: "a run is already in progress" });
    const games = Math.max(1, Math.floor(options.games));

    options.onStatus?.("loading the cards...");
    let manifest: DeckManifest;
    let cards: Map<string, unknown>;
    try {
      const loaded = await irClient.load(decklists.slice(0, 4));
      manifest = loaded.manifest;
      cards = new Map(loaded.cards);
    } catch (err) {
      return empty({ error: `couldn't load the cards: ${err instanceof Error ? err.message : String(err)}` });
    }
    const unknown = manifest.decks.flatMap((d) => d.unresolved);
    const first = manifest.decks[0];
    if (first === undefined || (first.cards.length === 0 && first.commanders.length === 0)) {
      return empty({ error: unknown.length ? `no cards found (unknown: ${unknown.slice(0, 5).join(", ")})` : "the first deck is empty" });
    }

    const setup: RunSetup = {
      // Seat 0 is the deck being measured; missing seats are copies of it,
      // as the server does.
      decks: Array.from({ length: 4 }, (_, seat) => manifest.decks[seat] ?? first),
      cards: Object.fromEntries(cards) as RunSetup["cards"],
      runSeed: Math.max(0, Math.floor(options.seed)) >>> 0,
      maxRounds: options.maxRounds ?? 25,
    };

    this.pool ??= new WorkerPool(defaultWorkers());
    // A game that runs past the watchdog, or a worker that dies, is recorded
    // as crashed with its seed and a fresh worker carries on: the run always
    // finishes (see pool.ts).
    const running = this.pool.run(setup, games, options.onProgress, DEFAULT_ENGINE, {
      gameTimeoutMs: (options.gameTimeoutSeconds ?? DEFAULT_GAME_TIMEOUT_MS / 1000) * 1000,
      engineBuild: ENGINE_BUILD,
      log: (message) => console.warn(`[goldfly] ${message}`),
    });
    this.cancelRun = running.cancel;
    let result: RunResult;
    try {
      result = await running.result;
    } catch (err) {
      return empty({ error: `the run failed: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      this.cancelRun = null;
    }

    const played = [...result.records, ...result.crashed];
    if (played.length === 0) return empty({ cancelled: true });
    const end = result.complete ? games : Math.max(...played.map((g) => g.index)) + 1;

    // Upload: a stored run is History and the spot check. The results stand
    // even if it fails.
    options.onStatus?.("saving the run...");
    let runId: string | null = null;
    let uploadError: string | null = null;
    let reload = false;
    try {
      runId = (await uploadRun(runUpload(manifest, setup, result, { start: 0, end }, 0, {
        deckName: options.deckName ?? "",
        heroDecklist: decklists[0] ?? "",
      }))).id;
    } catch (err) {
      uploadError = err instanceof Error ? err.message : String(err);
      reload = err instanceof UploadRefused && err.reload;
    }

    const report = buildReport(result.records, result.crashed, { hero: 0, seats: setup.decks.length });
    const payload = reportPayload(report, {
      unparsedCards: unparsedCards(first, cards as never),
    });
    const stored: StoredRun = {
      run: { id: runId ?? "", hero: 0, game_start: 0, game_end: end },
      records: result.records,
      crashed: result.crashed,
      setup,
      unverifiable: "",
    };
    return { payload: result.complete ? payload : { ...payload, partial: true }, stored, runId, uploadError, reload };
  }

  terminate(): void {
    this.pool?.terminate();
    this.pool = null;
  }
}
