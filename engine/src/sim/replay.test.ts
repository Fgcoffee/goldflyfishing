/// <reference types="vite/client" />
// The spot check's replay: the same seed gives the same digest as the run
// that produced it, with either engine; a game that throws is an error entry.
import { describe, expect, it } from "vitest";
import type { CardDocument } from "../ir";
import { seedFor } from "../kernel/random";
import { DEFAULT_ENGINE, ENGINES } from "./engines";
import { replayGames } from "./replay";
import type { DeckSpec, RunSetup } from "./runner";

const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });
function benchSetup(): RunSetup {
  const cards: Record<string, CardDocument> = {};
  let decks: Record<string, DeckSpec> = {};
  for (const [path, doc] of Object.entries(FILES)) {
    if (path.endsWith("/decks.json")) decks = doc as Record<string, DeckSpec>;
    else cards[(doc as CardDocument).oracle_id] = doc as CardDocument;
  }
  return { decks: ["aggro", "combo", "control", "stax"].map((k) => decks[k]!), cards, runSeed: 7, maxRounds: 4 };
}

describe("replayGames", () => {
  const setup = benchSetup();
  const games = [0, 1, 2].map((index) => ({ index, seed: seedFor(setup.runSeed, index) }));

  it.each(["stub", "rules"] as const)("the %s engine replays each game to its digest", (engine) => {
    const results = replayGames({ engine, setup, games });
    expect(results.map((r) => ("digest" in r ? r.digest : r.error))).toEqual(
      games.map((g) => ENGINES[engine].play(setup, g.index, g.seed).digest),
    );
  });

  it("defaults to the engine the page plays", () => {
    const [result] = replayGames({ setup, games: games.slice(0, 1) });
    expect(result).toEqual({ ...games[0], digest: ENGINES[DEFAULT_ENGINE].play(setup, 0, games[0]!.seed).digest });
  });

  it("a game that throws is reported, not fatal", () => {
    const broken: RunSetup = { ...setup, decks: [{ commanders: [], cards: [{ oracle_id: "no-such-card", count: 1 }] }] };
    const [result] = replayGames({ engine: "rules", setup: broken, games: games.slice(0, 1) });
    expect(result && "error" in result ? result.error : "").toMatch(/no IR for card no-such-card/);
  });
});
