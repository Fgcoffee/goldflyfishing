/**
 * Replaying stored games for the server's spot check: given a run's setup
 * and some of its games, play each again and report its digest. A game that
 * replays to a different digest than the one uploaded means a tampered or
 * broken client (or an engine that reads unordered state).
 *
 * Pure: the Node CLI (src/cli/replay.ts) only reads stdin and writes stdout.
 */

import { DEFAULT_ENGINE, ENGINES, type EngineName } from "./engines";
import type { RunSetup } from "./runner";

export interface ReplayRequest {
  /** Defaults to DEFAULT_ENGINE: the one the page played with. */
  readonly engine?: EngineName;
  readonly setup: RunSetup;
  readonly games: readonly { readonly index: number; readonly seed: number }[];
}

export type ReplayResult =
  | { readonly index: number; readonly seed: number; readonly digest: string }
  | { readonly index: number; readonly seed: number; readonly error: string };

export function replayGames(request: ReplayRequest): ReplayResult[] {
  const name = request.engine ?? DEFAULT_ENGINE;
  const engine = ENGINES[name];
  if (engine === undefined) throw new Error(`no engine named ${String(name)}`);
  return request.games.map(({ index, seed }) => {
    try {
      return { index, seed, digest: engine.play(request.setup, index, seed).digest };
    } catch (error) {
      return { index, seed, error: error instanceof Error ? `${error.name}: ${error.message}` : String(error) };
    }
  });
}
