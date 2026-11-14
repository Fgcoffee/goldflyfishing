import { describe, expect, it } from "vitest";
import type { GameRecord } from "./records";
import { summarize } from "./summary";

const game = (over: Partial<GameRecord>): GameRecord => ({
  index: 0, seed: 0, turns: 40, rounds: 10, winner: 0, winReason: "LIFE", eliminations: [],
  commanderLanded: [null, null], mulligans: [0, 0], openingHand: [], drawn: [], cast: [], turnsSeries: [], removal: [], runaway: "", loopDraw: false, digest: "",
  ...over,
});

describe("summarize", () => {
  it("counts wins, stalls and rounds per seat", () => {
    const summary = summarize(
      [
        game({ winner: 0, rounds: 8, commanderLanded: [3, null], mulligans: [1, 0] }),
        game({ winner: 0, rounds: 12, commanderLanded: [5, 4] }),
        game({ winner: 1, rounds: 9 }),
        game({ winner: null, rounds: 25, winReason: "STALL_OUT" }),
      ],
      [{ index: 4, seed: 1, error: "TypeError: x", detail: "" }],
    );
    expect(summary).toMatchObject({ games: 4, crashed: 1, stalls: 1, stallRate: 0.25 });
    expect(summary.seats[0]).toEqual({
      seat: 0, wins: 2, winRate: 0.5, averageWinRound: 10,
      commanderLanded: 2, averageCommanderRound: 4, mulliganRate: 0.25,
    });
    expect(summary.seats[1]).toMatchObject({ wins: 1, averageWinRound: 9, commanderLanded: 1, averageCommanderRound: 4 });
  });

  it("is all zeros and nulls for no games", () => {
    expect(summarize([])).toEqual({ games: 0, crashed: 0, stalls: 0, stallRate: 0, seats: [] });
  });
});
