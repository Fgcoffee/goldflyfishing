import { DEFAULT_ENGINE, ENGINES, runGames, type RunSetup } from "@goldfly/engine/sim";
import { describe, expect, it } from "vitest";
import { resultsFor, seedOf, type StoredRun } from "../src/site/entry";
import { ReplayHandler } from "../src/site/replay_worker_handle";

/** Real parsed cards from schema/fixtures/. */
const files = import.meta.glob<{ oracle_id: string; name: string }>("../../schema/fixtures/*.json", { eager: true, import: "default" });
const byName = new Map(Object.values(files).map((c) => [c.name, c]));
const id = (name: string) => byName.get(name)!.oracle_id;
const setup: RunSetup = {
  decks: [0, 1, 2, 3].map(() => ({
    commanders: [],
    cards: [
      { oracle_id: id("Forest"), count: 40 },
      { oracle_id: id("Grizzly Bears"), count: 30 },
      { oracle_id: id("Llanowar Elves"), count: 29 },
    ],
  })),
  cards: Object.fromEntries(["Forest", "Grizzly Bears", "Llanowar Elves"].map((n) => [id(n), byName.get(n) as never])),
  runSeed: 11,
  maxRounds: 5,
};

async function storedRun(): Promise<StoredRun> {
  const result = await runGames(ENGINES[DEFAULT_ENGINE], setup, 0, 3);
  return {
    run: { id: "r1", hero: 0, game_start: 0, game_end: 3 },
    records: result.records,
    crashed: result.crashed,
    setup,
    unverifiable: "",
  };
}

describe("a stored browser run on the website's screens", () => {
  it("its report has the Results screen's shape", async () => {
    const report = resultsFor(await storedRun());
    expect(report.games).toBe(3);
    expect(report).toHaveProperty("win_rate");
    expect(report).toHaveProperty("cards");
    expect(report.unparsed_cards).toEqual([]);
  }, 60_000);

  it("each game's seed is the one it was played from; a game the run doesn't hold has none", async () => {
    const stored = await storedRun();
    expect(seedOf(stored, 1)).toBe(stored.records[1]!.seed);
    expect(seedOf(stored, 7)).toBeNull();
  }, 60_000);

  it("the replay worker plays the recorded game again, then hands out its boards", async () => {
    const stored = await storedRun();
    const handler = new ReplayHandler();
    expect(handler.handle({ type: "board", id: 1, index: 0 }).payload).toEqual({ error: "no replay yet" });
    const game = stored.records[2]!;
    const replay = handler.handle({ type: "replay", id: 2, setup, index: 2, seed: game.seed, hero: 0 });
    expect(replay.id).toBe(2);
    expect(replay.payload.digest).toBe(game.digest);
    expect(replay.payload.game_index).toBe(2);
    const frames = replay.payload.board_frames as number[];
    const board = handler.handle({ type: "board", id: 3, index: frames.length - 1 }).payload;
    expect(board.index).toBe(frames.length - 1);
    expect(board.frame).toBe(frames.at(-1));
  }, 60_000);

  it("a replay that crashes says so instead of throwing", () => {
    const reply = new ReplayHandler().handle({ type: "replay", id: 4, setup: { ...setup, decks: [] }, index: 0, seed: 1, hero: 0 });
    expect(String(reply.payload.error)).toContain("the replay crashed");
  });
});
