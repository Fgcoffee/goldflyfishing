import { describe, expect, it } from "vitest";
import { seedFor } from "../kernel/random";
import type { GameEngine, RunSetup } from "./runner";
import { mergeResults, runGames, splitRange } from "./runner";
import { stubEngine } from "./stub";

const card = (id: string, name: string) =>
  ({ ir_version: 1, oracle_id: id, name, card_hash: "h", faces: [] }) as never;

const setup: RunSetup = {
  decks: [0, 1, 2, 3].map((seat) => ({
    commanders: [`c${seat}`],
    cards: [
      { oracle_id: "a", count: 30 },
      { oracle_id: "b", count: 30 },
      { oracle_id: `x${seat}`, count: 39 },
    ],
  })),
  cards: {
    a: card("a", "Island"),
    b: card("b", "Sol Ring"),
    x0: card("x0", "Card Zero"),
    x1: card("x1", "Card One"),
    x2: card("x2", "Card Two"),
    x3: card("x3", "Card Three"),
  },
  runSeed: 12345,
  maxRounds: 25,
};

describe("runGames", () => {
  it("gives every game the run's seed for its index", async () => {
    const result = await runGames(stubEngine, setup, 5, 9);
    expect(result.records.map((r) => r.index)).toEqual([5, 6, 7, 8]);
    expect(result.records.map((r) => r.seed)).toEqual([5, 6, 7, 8].map((i) => seedFor(12345, i)));
    expect(result.complete).toBe(true);
  });

  it("replays a game identically from its seed", async () => {
    const one = await runGames(stubEngine, setup, 0, 20);
    const two = await runGames(stubEngine, setup, 0, 20);
    expect(two).toEqual(one);
    expect(new Set(one.records.map((r) => r.digest)).size).toBe(20);
  });

  it("gives the same games however the range is split", async () => {
    const whole = await runGames(stubEngine, setup, 0, 31);
    const parts = await Promise.all(splitRange(31, 4).map(([s, e]) => runGames(stubEngine, setup, s, e)));
    expect(mergeResults(parts.reverse())).toEqual(whole);
  });

  it("leaves a crashed game out and reports it", async () => {
    const flaky: GameEngine = {
      name: "flaky",
      play: (s, index, seed) => {
        if (index === 2) throw new TypeError("x is undefined");
        return stubEngine.play(s, index, seed);
      },
    };
    const result = await runGames(flaky, setup, 0, 4);
    expect(result.records.map((r) => r.index)).toEqual([0, 1, 3]);
    expect(result.crashed).toHaveLength(1);
    expect(result.crashed[0]).toMatchObject({ index: 2, seed: seedFor(12345, 2), error: "TypeError: x is undefined" });
  });

  it("stops between games when asked, and says it is incomplete", async () => {
    let played = 0;
    const result = await runGames(stubEngine, setup, 0, 100, {
      onGame: () => (played += 1),
      shouldStop: () => played >= 3,
    });
    expect(result.records).toHaveLength(3);
    expect(result.complete).toBe(false);
  });

  it("pauses between games so a worker can read messages", async () => {
    let pauses = 0;
    await runGames(stubEngine, setup, 0, 10, { pause: async () => void (pauses += 1), yieldEvery: 3 });
    expect(pauses).toBe(3); // after games 3, 6 and 9, not after the last
  });
});

describe("splitRange", () => {
  it("covers every game once, in near-equal parts", () => {
    const ranges = splitRange(10, 3);
    expect(ranges).toEqual([[0, 3], [3, 6], [6, 10]]);
    expect(splitRange(2, 8)).toEqual([[0, 1], [1, 2]]);
    expect(splitRange(0, 4)).toEqual([]);
  });
});

describe("stub engine", () => {
  it("refuses a deck whose cards have no IR", () => {
    const broken = { ...setup, cards: {} };
    expect(() => stubEngine.play(broken, 0, 1)).toThrow("no IR for card");
  });
});
