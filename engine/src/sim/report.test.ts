import { describe, expect, it } from "vitest";
import type { GameRecord } from "./records";
import {
  METRICS, MIN_SAMPLE, averageWinRound, buildReport, worstSource, impact, margin, reliable, stallRate, topCards, winRate, worstCards,
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

// Python stats.py _removal, _series, runaways and loop draws.
describe("removal, series, runaways and loop draws", () => {
  const snap = (player: number, turn: number, lands: number) => ({
    turn, player, life: 40, lands, manaAvailable: lands, cardsInHand: 7, permanents: lands, boardPower: 0, creatures: 0,
  });
  const lost = (victim: string, source: string, how: string) => ({ turn: 3, victim, victimController: 0, source, sourceController: 1, how });

  it("removal is grouped by victim, most losses first, with sources and methods", () => {
    next = 0;
    const report = buildReport([
      game({ removal: [lost("Krenko", "Swords", "exiled"), lost("Bears", "Bolt", "died")] }),
      game({ removal: [lost("Krenko", "Swords", "exiled"), lost("Krenko", "Doom Blade", "destroyed")] }),
    ]);
    expect(report.removal.map((t) => [t.victim, t.losses])).toEqual([["Krenko", 3], ["Bears", 1]]);
    expect(report.removal[0]!.bySource).toEqual({ Swords: 2, "Doom Blade": 1 });
    expect(report.removal[0]!.byMethod).toEqual({ exiled: 2, destroyed: 1 });
    expect(worstSource(report.removal[0]!)).toEqual(["Swords", 2]);
  });

  it("a series averages over the games that reached each turn, and names the game nearest each mean", () => {
    next = 0;
    const report = buildReport([
      game({ turnsSeries: [snap(0, 1, 1), snap(0, 2, 2)] }),
      game({ turnsSeries: [snap(0, 1, 1), snap(0, 2, 4), snap(0, 3, 5)] }),
      game({ turnsSeries: [snap(0, 1, 2)] }),
    ]);
    const lands = report.series.find((s) => s.metric === "lands" && s.player === 0)!;
    expect(lands.turns).toEqual([1, 2, 3]);
    expect(lands.values[0]).toBeCloseTo(4 / 3);
    expect(lands.values.slice(1)).toEqual([3, 5]);
    expect(lands.samples).toEqual([3, 2, 1]);
    expect(lands.examples[0]).toEqual([0, 1, 2]);
    // Turn 2's mean is 3: games 0 (2) and 1 (4) tie, the lower index wins.
    expect(lands.representative[1]).toBe(0);
    expect(lands.representativeValue[1]).toBe(2);
    expect(report.series.map((s) => s.metric)).toEqual([...METRICS].sort());
  });

  it("runaways are counted by what looped; a loop draw is not a stall-out", () => {
    next = 0;
    const report = buildReport([
      game({ winner: null, runaway: "Card X's ability" }),
      game({ winner: null, loopDraw: true, winReason: "DRAW" }),
      game({ winner: null }),
    ]);
    expect(report.runaways).toEqual({ "Card X's ability": 1 });
    expect(report.loopDraws).toBe(1);
    expect(report.stalls).toBe(2);
    expect(report.winReasons).toEqual({ DRAW: 1, STALL_OUT: 2 });
  });
});
