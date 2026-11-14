// End to end on real card IR: the four bench decks from schema/samples,
// played through `play(setup, index, seed)` by agents that play lands, cast
// spells and attack. Not a Python port: the gate for a full game.
/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import type { CardDocument } from "../../src/ir";
import { EventKind } from "../../src/kernel/events";
import type { Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { seedFor } from "../../src/kernel/random";
import { ActionKind, PassingAgent, type Action, type Agent } from "../../src/rules/cr100_game_concepts/priority";
import { play, playGame, rulesEngine } from "../../src/rules/play";
import { SimpleAgent } from "../../src/ai/simple";
import type { DeckSpec, RunSetup } from "../../src/sim/runner";

// The bench decks' cards and lists, exported by `python -m mtgfish.tools.ir_export samples`.
const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });

function loadSetup(maxRounds: number): RunSetup {
  const cards: Record<string, CardDocument> = {};
  let decks: Record<string, DeckSpec> = {};
  for (const [path, doc] of Object.entries(FILES)) {
    if (path.endsWith("/decks.json")) decks = doc as Record<string, DeckSpec>;
    else cards[(doc as CardDocument).oracle_id] = doc as CardDocument;
  }
  return { decks: ["aggro", "combo", "control", "stax"].map((k) => decks[k]!), cards, runSeed: 7, maxRounds };
}

/** Plays a land or casts a spell whenever it can, and attacks the next player with everything. */
function eager(): Agent {
  return Object.assign(new PassingAgent(), {
    chooseAction: (_game: Game, _player: number, legal: readonly Action[]): Action =>
      legal.find((a) => a.kind === ActionKind.PLAY_LAND) ?? legal.find((a) => a.kind === ActionKind.CAST_SPELL) ?? legal[0]!,
    declareAttackers: (game: Game, player: number, candidates: readonly GameObject[]) => {
      const target = game.players.find((p) => p.id !== player && !p.hasLost)?.id;
      return new Map(target === undefined ? [] : candidates.map((c) => [c.id, target] as const));
    },
  });
}

describe("play(setup, index, seed) on the bench decks", () => {
  const setup = loadSetup(8);

  it("plays games to the end without crashing, and a seed always gives the same game", () => {
    for (let index = 0; index < 4; index += 1) {
      const seed = seedFor(setup.runSeed, index);
      const first = play(setup, index, seed, { agentFor: eager });
      const again = play(setup, index, seed, { agentFor: eager });
      expect(again.digest).toBe(first.digest);
      expect(first.turns).toBeGreaterThan(0);
      expect(first.openingHand).toHaveLength(7);
    }
  });

  it("different seeds give different games", () => {
    const a = play(setup, 0, 1, { agentFor: eager });
    const b = play(setup, 0, 2, { agentFor: eager });
    expect(a.digest).not.toBe(b.digest);
  });

  it("cards are cast and creatures attack", () => {
    const seen = new Map<number, number>();
    const { record } = playGame(setup, 0, seedFor(setup.runSeed, 0), {
      agentFor: eager,
      watch: (_g, e) => void seen.set(e.kind, (seen.get(e.kind) ?? 0) + 1),
    });
    expect(record.cast.length).toBeGreaterThan(3);
    expect(seen.get(EventKind.ATTACKS) ?? 0).toBeGreaterThan(0);
    expect(seen.get(EventKind.COMBAT_DAMAGE_DEALT) ?? 0).toBeGreaterThan(0);
  });

  it("the runner's engine seats SimpleAgent by default, as Python's runner does", () => {
    const { game } = playGame(setup, 0, 3);
    expect([...game.agents.values()].every((a) => a instanceof SimpleAgent)).toBe(true);
    // Eight rounds is too short for these decks to finish a game.
    expect(rulesEngine.play(setup, 0, 3).winReason).toBe("STALL_OUT");
  });
});

describe("the record's per-turn series and removal (Python sim/observer.py)", () => {
  const setup = loadSetup(6);
  it("each living player is sampled at the end of each of their own turns", () => {
    const { record, game } = playGame(setup, 0, seedFor(setup.runSeed, 0), { agentFor: eager });
    expect(record.turnsSeries.length).toBeGreaterThan(0);
    for (const seat of game.players.map((p) => p.id)) {
      const turns = record.turnsSeries.filter((s) => s.player === seat).map((s) => s.turn);
      expect(turns).toEqual(turns.map((_, i) => i + 1));
    }
    const first = record.turnsSeries[0]!;
    expect(first.permanents).toBeGreaterThanOrEqual(first.lands);
    expect(first.permanents).toBeGreaterThanOrEqual(first.creatures);
    expect(record.runaway).toBe("");
  });

  it("removal names only the hero's objects, taken by another player", () => {
    for (let index = 0; index < 4; index += 1) {
      const { record } = playGame(setup, index, seedFor(setup.runSeed, index), { agentFor: eager });
      for (const r of record.removal) {
        expect(r.victimController).toBe(0);
        expect(r.sourceController).not.toBe(0);
      }
    }
  });
});
