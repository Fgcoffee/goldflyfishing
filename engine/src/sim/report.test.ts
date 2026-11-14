import { describe, expect, it } from "vitest";
import type { GameRecord } from "./records";
import {
  MIN_SAMPLE, averageWinRound, buildReport, impact, margin, reliable, stallRate, topCards, winRate, worstCards,
  type CardImpact,
} from "./report";

let next = 0;
const game = (over: Partial<GameRecord>): GameRecord => ({
  index: next++, seed: 0, turns: 40, rounds: 10, winner: 0, winReason: "LIFE", eliminations: [],
  commanderLanded: [null, null, null, null], mulligans: [0, 0, 0, 0], openingHand: [], drawn: [], cast: [],
  turnsSeries: [], removal: [], runaway: "", loopDraw: false, digest: "", ...over,
});

describe("buildReport outcomes", () => {
  it("counts wins, stalls and reasons for the hero", () => {
    next = 0;
    const report = buildReport([
      game({ winner: 0, rounds: 6, turns: 22, winReason: "COMMANDER_DAMAGE" }),
      game({ winner: 0, rounds: 8, turns: 30 }),
      game({ winner: null, rounds: 25, winReason: "STALL_OUT" }),
      game({ winner: 2, eliminations: [{ player: 1, turn: 9, reason: "LIFE" }, { player: 0, turn: 20, reason: "POISON" }] }),
      game({ winner: 3, eliminations: [] }),
    ]);
    expect(report).toMatchObject({ games: 5, wins: 2, stalls: 1, winRounds: [6, 8], winTurns: [22, 30] });
    expect(report.winReasons).toEqual({ COMMANDER_DAMAGE: 1, LIFE: 1, STALL_OUT: 1 });
    expect(report.lossReasons).toEqual({ POISON: 1 });
    expect(report.byWinRound).toEqual({ 6: [0], 8: [1] });
    expect(winRate(report)).toBe(0.4);
    expect(stallRate(report)).toBe(0.2);
    expect(averageWinRound(report)).toBe(7);
  });

  it("builds the commander histogram and keeps the games behind each bar", () => {
    next = 0;
    const report = buildReport([
      game({ commanderLanded: [3, null, null, null] }),
      game({ commanderLanded: [3, 2, null, null] }),
      game({ commanderLanded: [5, null, null, null] }),
      game({}),
    ]);
    expect(report.commanderLanded).toEqual({ 3: 2, 5: 1 });
    expect(report.commanderLandedGames).toEqual({ 3: [0, 1], 5: [2] });
    expect(report.commanderNever).toBe(1);
  });

  it("measures for any hero seat", () => {
    const report = buildReport([game({ winner: 2 }), game({ winner: 0 })], [], { hero: 2 });
    expect(report.wins).toBe(1);
    expect(report.hero).toBe(2);
  });

  it("lists crashed games without their stacks", () => {
    const report = buildReport([], [{ index: 4, seed: 9, error: "TypeError: x", detail: "stack..." }]);
    expect(report.crashed).toEqual([{ index: 4, seed: 9, error: "TypeError: x" }]);
    expect(report.games).toBe(0);
  });
});

describe("card impact", () => {
  it("compares win rate drawn against not drawn, counting a card once a game", () => {
    next = 0;
    const records = [
      game({ winner: 0, drawn: ["Sol Ring", "Island", "Island"], openingHand: ["Sol Ring"],
             cast: [{ name: "Sol Ring", round: 1 }] }),
      game({ winner: 0, drawn: ["Sol Ring"], cast: [{ name: "Sol Ring", round: 3 }] }),
      game({ winner: 1, drawn: ["Island"] }),
      game({ winner: 1, drawn: ["Sol Ring", "Island"] }),
    ];
    const cards = buildReport(records).cards;
    expect(cards.map((c) => c.name)).toEqual(["Island", "Sol Ring"]);
    const sol = cards[1] as CardImpact;
    expect(sol).toEqual({
      name: "Sol Ring", drawn: 3, drawnWins: 2, notDrawn: 1, notDrawnWins: 0,
      cast: 2, averageCastRound: 2, openingHand: 1,
    });
    expect(impact(sol)).toBeCloseTo(2 / 3);
    const island = cards[0] as CardImpact;
    expect(island.drawn).toBe(3); // not 4: twice in game 0 counts once
  });

  it("gives a margin, and 1 when one side has no games", () => {
    const card: CardImpact = { name: "x", drawn: 100, drawnWins: 60, notDrawn: 100, notDrawnWins: 40,
                               cast: 0, averageCastRound: 0, openingHand: 0 };
    expect(margin(card)).toBeCloseTo(1.96 * Math.sqrt((0.6 * 0.4) / 100 + (0.4 * 0.6) / 100));
    expect(margin({ ...card, notDrawn: 0, notDrawnWins: 0 })).toBe(1);
  });

  it("ranks only cards seen enough both ways", () => {
    next = 0;
    const records: GameRecord[] = [];
    for (let i = 0; i < 2 * MIN_SAMPLE; i += 1) {
      const drawnGood = i % 2 === 0;
      records.push(game({
        winner: drawnGood ? 0 : 1,
        drawn: [drawnGood ? "Good" : "Bad", ...(i < 3 ? ["Rare"] : [])],
      }));
    }
    const report = buildReport(records);
    expect(reliable(report.cards.find((c) => c.name === "Rare") as CardImpact)).toBe(false);
    expect(topCards(report).map((c) => c.name)).toEqual(["Good", "Bad"]);
    expect(worstCards(report).map((c) => c.name)).toEqual(["Bad", "Good"]);
  });
});
