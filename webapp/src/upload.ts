/**
 * Sending a finished (or cancelled) browser run back to the server, which
 * stores it with everything needed to replay any game: `/api/browser-runs`.
 *
 * The body is gzipped with the browser's own CompressionStream. A run the
 * server refuses (for example, a game whose seed isn't the run's) is
 * reported, never retried.
 */

import type { RunResult, RunSetup } from "@goldfly/engine/sim";
import type { DeckManifest } from "./ir";
import type { Fetch } from "./ir";

/** Fingerprint of the engine source this bundle was built from (vite.config.ts). */
declare const __ENGINE_BUILD__: string;
export const ENGINE_BUILD: string = typeof __ENGINE_BUILD__ === "string" ? __ENGINE_BUILD__ : "dev";

export interface StoredRun {
  readonly id: string;
  readonly games: number;
  readonly crashed: number;
  readonly spot_check: string;
  readonly signed_in: boolean;
}

/** What the server stores for one run (see mtgfish/web/browser_runs.py). */
export function runUpload(
  manifest: DeckManifest,
  setup: RunSetup,
  result: RunResult,
  range: { readonly start: number; readonly end: number },
  hero = 0,
): Record<string, unknown> {
  return {
    run_seed: setup.runSeed,
    game_start: range.start,
    game_end: range.end,
    hero,
    max_rounds: setup.maxRounds,
    complete: result.complete,
    engine_build: ENGINE_BUILD,
    ir_build: manifest.build,
    snapshot: manifest.snapshot,
    decks: setup.decks.map((deck) => ({
      commanders: [...deck.commanders],
      cards: deck.cards.map((entry) => ({
        oracle_id: entry.oracle_id,
        card_hash: manifest.cards[entry.oracle_id]?.card_hash ?? "",
        count: entry.count,
      })),
    })),
    records: result.records,
    crashed: result.crashed,
  };
}

async function gzip(text: string): Promise<Blob> {
  const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Response(stream).blob();
}

export async function uploadRun(
  body: Record<string, unknown>,
  options: { readonly base?: string; readonly fetch?: Fetch } = {},
): Promise<StoredRun> {
  const send = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const response = await send(`${options.base ?? ""}/api/browser-runs`, {
    method: "POST",
    headers: { "content-type": "application/json", "content-encoding": "gzip" },
    body: await gzip(JSON.stringify(body)),
    credentials: "same-origin",
  });
  const reply = (await response.json()) as StoredRun | { error?: string };
  if (!response.ok) {
    throw new Error(("error" in reply && reply.error) || `the server didn't store the run (${response.status})`);
  }
  return reply as StoredRun;
}
