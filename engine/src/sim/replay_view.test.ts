/**
 * The replay screen's view of a game (mtgfish/sim/replay.py), played again
 * from its seed on the bench decks. The point above all: the replayed game
 * is the run's game, digest for digest, with the log and the film on.
 */

import { describe, expect, it } from "vitest";
import type { CardDocument } from "../ir";
import { seedFor } from "../kernel/random";
import { rulesEngine } from "../rules/play";
import { atFrame } from "./board";
import { buildTurns, replayGame, shownAt } from "./replay_view";
import type { DeckSpec, RunSetup } from "./runner";

const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });

function benchSetup(maxRounds: number): RunSetup {
  const cards: Record<string, CardDocument> = {};
  let decks: Record<string, DeckSpec> = {};
  for (const [path, doc] of Object.entries(FILES)) {
    if (path.endsWith("/decks.json")) decks = doc as Record<string, DeckSpec>;
    else cards[(doc as CardDocument).oracle_id] = doc as CardDocument;
  }
  return { decks: ["aggro", "combo", "control", "stax"].map((k) => decks[k]!), cards, runSeed: 3, maxRounds };
}

describe("replayGame on the bench decks", () => {
  const setup = benchSetup(5);
  const seed = seedFor(setup.runSeed, 1);
  const played = rulesEngine.play(setup, 1, seed);
  const view = replayGame(setup, 1, seed);

  it("is the run's game: same digest, turns and winner, with the log and the film on", () => {
    expect(view.digest).toBe(played.digest);
    expect(view.turns).toBe(played.turns);
    expect(view.winner).toBe(played.winner);
  });

  it("opens with the setup block, then numbers each seat's turns from one", () => {
    expect(view.turn_list[0]!.is_setup).toBe(true);
    expect(view.turn_list[0]!.label).toBe("Before the game");
    const seen = new Map<number, number>();
    for (const t of view.turn_list.slice(1)) {
      seen.set(t.player, (seen.get(t.player) ?? 0) + 1);
      expect(t.player_turn).toBe(seen.get(t.player));
      expect(t.label).toBe(`${t.player_name} - turn ${t.player_turn}`);
    }
    // The turns tile the frames.
    view.turn_list.forEach((t, i) => {
      if (i > 0) expect(t.start).toBe(view.turn_list[i - 1]!.end);
    });
    expect(view.turn_list.at(-1)!.end).toBe(view.frames.length);
  });

  it("carries a seat per player, the hero marked, and the turns each took", () => {
    expect(view.seats.map((s) => s.is_hero)).toEqual([true, false, false, false]);
    for (const seat of view.seats) {
      expect(seat.turns_taken).toBe(view.turn_list.filter((t) => t.player === seat.id).length);
    }
    expect(view.outcome).not.toBe("");
    expect(view.final_board.startsWith(`Turn ${view.turns}`)).toBe(true);
  });

  it("films the board: cards interned once, no two consecutive boards alike, frames in order", () => {
    const film = view.film;
    expect(film.snapshots.length).toBeGreaterThan(0);
    expect(new Set(film.cards.map((c) => JSON.stringify(c))).size).toBe(film.cards.length);
    for (let i = 1; i < film.snapshots.length; i++) {
      const [a, b] = [film.snapshots[i - 1]!, film.snapshots[i]!];
      expect(b.frame).toBeGreaterThanOrEqual(a.frame);
      expect(JSON.stringify([a.seats, a.stack])).not.toBe(JSON.stringify([b.seats, b.stack]));
    }
    const permanentCards = film.snapshots.flatMap((s) => s.seats.flatMap((seat) => seat.permanents.map((p) => p.card)));
    expect(Math.max(...permanentCards)).toBeLessThan(film.cards.length);
    // A replay never shows a hand.
    expect(film.snapshots.every((s) => s.seats.every((seat) => seat.hand_cards.length === 0))).toBe(true);
    expect(atFrame(film, view.frames.length)).toBe(film.snapshots.at(-1));
  });

  it("without boards, the same game and an empty film", () => {
    const bare = replayGame(setup, 1, seed, { boards: false });
    expect(bare.digest).toBe(played.digest);
    expect(bare.film.snapshots).toEqual([]);
  });
});

describe("buildTurns and detail levels (test_replay.py)", () => {
  const entry = (turn: number, active: number, kind = "info", player = active) =>
    ({ turn, phase: 0, step: 0, player, kind, text: kind, depth: 0, activePlayer: active }) as never;

  it("an opponent acting on your turn doesn't open a turn for them", () => {
    const { turns } = buildTurns([entry(0, 0, "setup"), entry(1, 0), entry(1, 0, "draw", 1), entry(2, 1)], new Map([[0, "You"], [1, "Opp"]]));
    expect(turns.map((t) => [t.player, t.player_turn])).toEqual([[-1, 0], [0, 1], [1, 1]]);
  });

  it("key shows only the story, normal hides only raw events, everything hides nothing", () => {
    expect(shownAt("cast", "key")).toBe(true);
    expect(shownAt("info", "key")).toBe(false);
    expect(shownAt("info", "normal")).toBe(true);
    expect(shownAt("event", "normal")).toBe(false);
    expect(shownAt("event", "everything")).toBe(true);
    expect(shownAt("something new", "normal")).toBe(true);
  });
});
