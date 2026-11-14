import { buildReport, runGames, stubEngine, type RunSetup } from "@goldfly/engine/sim";
import { describe, expect, it } from "vitest";
import { reportPayload, unparsedCards } from "../src/results";

/** The keys mtgfish/ui/bridge.py _report_payload sends, which app.js reads. */
const PYTHON_KEYS = [
  "average_win_round", "average_win_turn", "by_win_round", "cards", "commander_landed",
  "commander_landed_games", "commander_never", "crashed", "digest_mismatches", "games", "loop_draws",
  "loops", "loss_reasons", "removal", "runaways", "seats", "series", "stall_rate", "stalls",
  "unparsed_cards", "win_rate", "win_reasons", "wins",
];

const doc = (id: string, name: string) =>
  ({ ir_version: 2, oracle_id: id, name, card_hash: "h", faces: [] }) as never;

const setup: RunSetup = {
  decks: [0, 1, 2, 3].map((seat) => ({
    commanders: ["cmd"],
    cards: [{ oracle_id: "land", count: 40 }, { oracle_id: `s${seat}`, count: 59 }],
  })),
  cards: { land: doc("land", "Island"), cmd: doc("cmd", "Krenko"), s0: doc("s0", "A"), s1: doc("s1", "B"), s2: doc("s2", "C"), s3: doc("s3", "D") },
  runSeed: 3,
  maxRounds: 25,
};

describe("the Results payload", () => {
  it("has exactly the keys the server's payload has", async () => {
    const result = await runGames(stubEngine, setup, 0, 50);
    const payload = reportPayload(buildReport(result.records, result.crashed));
    expect(Object.keys(payload).sort()).toEqual(PYTHON_KEYS);
  });

  it("carries the numbers, snake_case, and survives JSON", async () => {
    const result = await runGames(stubEngine, setup, 0, 80);
    const report = buildReport(result.records, result.crashed);
    const payload = JSON.parse(JSON.stringify(reportPayload(report, { unparsedCards: ["X"], digestMismatches: [4] })));
    expect(payload.games).toBe(80);
    expect(payload.win_rate).toBeCloseTo(report.wins / 80);
    expect(payload.unparsed_cards).toEqual(["X"]);
    expect(payload.digest_mismatches).toEqual([4]);
    const card = payload.cards[0];
    expect(Object.keys(card).sort()).toEqual(["average_cast_turn", "cast", "drawn", "impact", "margin", "name", "reliable"]);
  });

  it("names per-turn metrics as the Python charts do", () => {
    const report = { ...buildReport([]), series: [{ metric: "manaAvailable", player: 0, turns: [1], values: [2], samples: [3], examples: [[0]], representative: [0], representativeValue: [2] }] } as never;
    const [series] = reportPayload(report).series as { metric: string; representative_value: number[] }[];
    expect(series?.metric).toBe("mana_available");
    expect(series?.representative_value).toEqual([2]);
  });

  it("lists the deck's partly read cards", () => {
    const cards = new Map([
      ["a", { name: "Read", faces: [{ fully_read: true }] }],
      ["b", { name: "Partly", faces: [{ fully_read: true }, { fully_read: false }] }],
    ]) as never;
    expect(unparsedCards({ commanders: ["a"], cards: [{ oracle_id: "b" }] }, cards)).toEqual(["Partly"]);
  });
});
