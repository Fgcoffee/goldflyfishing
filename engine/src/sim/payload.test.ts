/**
 * The Replay screen's JSON, from a browser run: the same keys Python's
 * Bridge sends (mtgfish/ui/bridge.py), so app.js reads either.
 */

import { describe, expect, it } from "vitest";
import type { CardDocument } from "../ir";
import { seedFor } from "../kernel/random";
import { rulesEngine } from "../rules/play";
import { boardPayload, parseStatus, replayPayload, TAPPED } from "./payload";
import { replayGame } from "./replay_view";
import type { DeckSpec, RunSetup } from "./runner";

const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });
const cards: Record<string, CardDocument> = {};
let decks: Record<string, DeckSpec> = {};
for (const [path, doc] of Object.entries(FILES)) {
  if (path.endsWith("/decks.json")) decks = doc as Record<string, DeckSpec>;
  else cards[(doc as CardDocument).oracle_id] = doc as CardDocument;
}
const setup: RunSetup = { decks: ["aggro", "combo", "control", "stax"].map((k) => decks[k]!), cards, runSeed: 5, maxRounds: 4 };

/** Python `Bridge.replay`'s keys (plus `digest`, for the page's check). */
const REPLAY_KEYS = [
  "game_index", "seed", "turns", "winner", "outcome", "final_board", "detail_levels", "key_kinds",
  "noise_kinds", "seats", "turns_taken", "frames", "board_cards", "board_frames", "digest",
];

describe("replayPayload and boardPayload", () => {
  const seed = seedFor(setup.runSeed, 0);
  const view = replayGame(setup, 0, seed);

  it("has Python's keys; turns_taken carry their labels; board cards their parse status", () => {
    const payload = replayPayload(view, cards);
    expect(Object.keys(payload).sort()).toEqual([...REPLAY_KEYS].sort());
    expect((payload.turns_taken as { label: string }[])[0]!.label).toBe("Before the game");
    const boardCards = payload.board_cards as { status: string; art: string }[];
    expect(boardCards.length).toBe(view.film.cards.length);
    for (const c of boardCards) expect(["ok", "partial", "blank"]).toContain(c.status);
    expect((payload.board_frames as number[]).length).toBe(view.film.snapshots.length);
    expect(payload.digest).toBe(rulesEngine.play(setup, 0, seed).digest);
  });

  it("a board is Python's short arrays with bit flags, clamped by index", () => {
    const last = boardPayload(view, 1e9) as { index: number; seats: { permanents: unknown[][] }[] };
    expect(last.index).toBe(view.film.snapshots.length - 1);
    const snapshot = view.film.snapshots[last.index]!;
    const first = snapshot.seats.flatMap((s) => s.permanents)[0];
    if (first !== undefined) {
      const row = last.seats.flatMap((s) => s.permanents)[0]!;
      expect(row.slice(0, 2)).toEqual([first.id, first.card]);
      expect(((row[2] as number) & TAPPED) !== 0).toBe(first.tapped);
    }
    expect(boardPayload({ ...view, film: { cards: [], snapshots: [] } }, 0)).toEqual({ error: "no replay yet" });
  });
});

describe("parseStatus", () => {
  it("a vanilla card is ok; nothing read is blank; some read is partial", () => {
    const doc = (abilities: object[]) => ({ faces: [{ abilities }] }) as unknown as CardDocument;
    expect(parseStatus(doc([]))).toEqual({ status: "ok", unread: [], recognised: [] });
    expect(parseStatus(doc([{ unparsed: true, text: "x" }]))).toEqual({ status: "blank", unread: ["x"], recognised: [] });
    expect(parseStatus(doc([{ unparsed: true, text: "x" }, { text: "y" }])).status).toBe("partial");
  });

  it("names the recognised-not-implemented (dummy) shapes, and they aren't gaps", () => {
    const doc = { faces: [{ abilities: [
      // A shape the engine doesn't carry out (made up: every registered shape is done by now).
      { $: "Ability", effects: [{ $: "Effect", kind: 120 }, { $: "Effect", kind: 998, rule: "some-future-shape" }] },
    ] }] } as unknown as CardDocument;
    expect(parseStatus(doc)).toEqual({ status: "ok", unread: [], recognised: ["some-future-shape"] });
  });

  it("a dummy the engine carries out (IMPLEMENTED_DUMMIES) isn't listed", () => {
    const doc = { faces: [{ abilities: [
      { $: "Ability", effects: [{ $: "Effect", kind: 998, rule: "untap-during-others-untap-step" }] },
    ] }] } as unknown as CardDocument;
    expect(parseStatus(doc)).toEqual({ status: "ok", unread: [], recognised: [] });
  });
});
