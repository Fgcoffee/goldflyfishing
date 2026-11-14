// Ported from mtgfish/rules/cr500_turn_structure/cr500_turn.py and
// cr117_priority.py: whole turns with passing agents.
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { EventKind, type GameEvent } from "../../src/kernel/events";
import { createObject, player, type Game } from "../../src/kernel/game";
import { logDigest } from "../../src/kernel/log";
import { addMana, manaKind, poolTotal } from "../../src/rules/cr100_game_concepts/mana_pool";
import { PassingAgent } from "../../src/rules/cr100_game_concepts/priority";
import { DEFAULT_TURN_OPTIONS, runGame, takeTurn } from "../../src/rules/cr500_turn_structure/turn";
import { Board, CARDS } from "./board";

function table(seed = 1): Board {
  const board = new Board();
  for (const p of board.game.players) {
    board.game.agents.set(p.id, new PassingAgent());
    for (let i = 0; i < 20; i += 1) createObject(board.game, CARDS["Forest"], p.id, Zone.LIBRARY);
  }
  board.game.activePlayer = 0;
  void seed;
  return board;
}

function watch(game: Game): GameEvent[] {
  const events: GameEvent[] = [];
  game.observer = (_g, e) => events.push(e);
  return events;
}

describe("turns (CR 500)", () => {
  it("each turn the active player draws, and turns pass in order", () => {
    const board = table();
    const game = board.game;
    takeTurn(game);
    expect(game.turn).toBe(1);
    expect(player(game, 0).hand).toHaveLength(1);
    expect(player(game, 1).hand).toHaveLength(0);
    const ended = runGame(game, { ...DEFAULT_TURN_OPTIONS, maxRounds: 2 });
    expect(ended).toBe(game);
    // takeTurn alone doesn't pass the turn: turns 1-2 are player 0's.
    expect(game.turn).toBe(4);
    expect(player(game, 0).hand).toHaveLength(3);
    expect(player(game, 1).hand).toHaveLength(1);
  });

  it("the first player can skip their first draw (CR 103.8a)", () => {
    const board = table();
    takeTurn(board.game, { ...DEFAULT_TURN_OPTIONS, firstPlayerSkipsFirstDraw: true });
    expect(player(board.game, 0).hand).toHaveLength(0);
  });

  it("steps run in order, with only the first main phase precombat (CR 505.1a)", () => {
    const board = table();
    const events = watch(board.game);
    takeTurn(board.game);
    const steps = events.filter((e) => e.kind === EventKind.STEP_BEGAN).map((e) => e.amount);
    // CR 508.8: nobody attacked, so no declare blockers or damage step.
    expect(steps).toEqual([Step.UNTAP, Step.UPKEEP, Step.DRAW, Step.MAIN, Step.BEGINNING_OF_COMBAT, Step.DECLARE_ATTACKERS, Step.END_OF_COMBAT, Step.MAIN, Step.END_STEP, Step.CLEANUP]);
    const phases = events.filter((e) => e.kind === EventKind.PHASE_BEGAN).map((e) => e.amount);
    expect(phases).toEqual([Phase.BEGINNING, Phase.PRECOMBAT_MAIN, Phase.COMBAT, Phase.POSTCOMBAT_MAIN, Phase.ENDING]);
  });

  it("untaps the active player's permanents and ends summoning sickness (CR 502.3, 302.6)", () => {
    const board = table();
    const forest = board.play("Forest", 0);
    const theirs = board.play("Forest", 1);
    forest.tapped = true;
    theirs.tapped = true;
    forest.summoningSick = true;
    takeTurn(board.game);
    expect(forest.tapped).toBe(false);
    expect(forest.summoningSick).toBe(false);
    expect(theirs.tapped).toBe(true);
  });

  it("discards down to seven in cleanup (CR 514.1)", () => {
    const board = table();
    for (let i = 0; i < 8; i += 1) createObject(board.game, CARDS["Forest"], 0, Zone.HAND);
    takeTurn(board.game);
    expect(player(board.game, 0).hand).toHaveLength(7);
    expect(player(board.game, 0).graveyard).toHaveLength(2);
  });

  it("mana empties as steps end (CR 500.4), and damage wears off in cleanup (CR 514.2)", () => {
    const board = table();
    const bears = board.play("Grizzly Bears", 0);
    bears.damage = 1;
    addMana(player(board.game, 0).manaPool, manaKind(Color.GREEN), 3);
    const events = watch(board.game);
    takeTurn(board.game);
    expect(poolTotal(player(board.game, 0).manaPool)).toBe(0);
    expect(events.some((e) => e.kind === EventKind.MANA_EMPTIED && e.amount === 3)).toBe(true);
    expect(bears.damage).toBe(0);
  });

  it("the most recent extra turn is taken first (CR 500.7)", () => {
    const board = table();
    const game = board.game;
    game.extraTurns = [1, 0];
    const actives: number[] = [];
    game.observer = (_g, e) => {
      if (e.kind === EventKind.TURN_BEGAN) actives.push(e.player);
    };
    runGame(game, { ...DEFAULT_TURN_OPTIONS, maxRounds: 2 });
    // Extra turns [1, 0]: 0's is taken first, then 1's, then play goes on.
    expect(actives).toEqual([0, 0, 1, 0]);
  });

  it("the same seed replays the same game, digest for digest", () => {
    const digests = [1, 2].map(() => {
      const board = table();
      runGame(board.game, { ...DEFAULT_TURN_OPTIONS, maxRounds: 3 });
      return logDigest(board.game.log);
    });
    expect(digests[0]).toBe(digests[1]);
  });

  it("a stall-out is logged at the turn cap", () => {
    const board = table();
    board.game.log.enabled = true;
    runGame(board.game, { ...DEFAULT_TURN_OPTIONS, maxRounds: 1 });
    expect(board.game.log.entries.at(-1)?.kind).toBe("stall");
    expect(board.game.gameOver).toBe(false);
  });
});
