// Ported from mtgfish/rules/cr100_game_concepts/actions.py (draw, discard,
// mill), Game.player_loses / player_wins, and cr611_durations.py.
import { describe, expect, it } from "vitest";
import { Duration, LossReason, Zone } from "../../src/kernel/enums";
import { createObject, player, type ContinuousEffect } from "../../src/kernel/game";
import { BENCH } from "../../src/kernel/relaxations";
import { discard, draw, mill, playerLoses, playerWins, shuffleLibrary } from "../../src/rules/cr100_game_concepts/actions";
import { expireAtCleanup, expireAtEndOfCombat, expireAtStartOfTurn } from "../../src/rules/cr600_spells_and_abilities/durations";
import { Board, CARDS } from "./board";

function stock(board: Board, owner: number, count: number): void {
  for (let i = 0; i < count; i += 1) createObject(board.game, CARDS["Forest"], owner, Zone.LIBRARY);
}

describe("drawing, discarding, milling (CR 121, 701.9, 701.17)", () => {
  it("draws from the top; an empty library sets the flag the SBA reads (CR 704.5b)", () => {
    const board = new Board();
    stock(board, 0, 2);
    const top = player(board.game, 0).library[0];
    const drawn = draw(board.game, 0, 3);
    expect(drawn).toHaveLength(2);
    expect(drawn[0]?.previousId).toBe(top);
    expect(player(board.game, 0).hand).toHaveLength(2);
    expect(player(board.game, 0).cardsDrawnThisTurn).toBe(2);
    expect(player(board.game, 0).attemptedDrawFromEmptyLibrary).toBe(true);
  });

  it("on the bench an empty library does nothing", () => {
    const board = new Board();
    board.game.relaxations = BENCH;
    draw(board.game, 0, 1);
    expect(player(board.game, 0).attemptedDrawFromEmptyLibrary).toBe(false);
  });

  it("discards to the graveyard and mills the top", () => {
    const board = new Board();
    stock(board, 0, 3);
    const [card] = draw(board.game, 0, 1);
    if (card === undefined) throw new Error("no card");
    expect(discard(board.game, card).zone).toBe(Zone.GRAVEYARD);
    expect(mill(board.game, 0, 5)).toBe(2);
    expect(player(board.game, 0).graveyard).toHaveLength(3);
  });

  it("shuffles deterministically from the game's seed", () => {
    const a = new Board();
    const b = new Board();
    for (const board of [a, b]) {
      stock(board, 0, 30);
      shuffleLibrary(board.game, 0);
    }
    expect(player(a.game, 0).library).toEqual(player(b.game, 0).library);
  });
});

describe("leaving the game (CR 104.3, 800.4)", () => {
  it("takes everything the player owns with them and returns stolen permanents", () => {
    const board = new Board();
    const theirs = board.play("Grizzly Bears", 1);
    const stolen = board.play("Grizzly Bears", 0);
    stolen.controller = 1;
    stolen.baseController = 1;
    playerLoses(board.game, 1, LossReason.LIFE);
    expect(board.game.objects.has(theirs.id)).toBe(false);
    expect(board.game.battlefield).toEqual([stolen.id]);
    expect(stolen.controller).toBe(0);
    expect(board.game.gameOver).toBe(true);
    expect(board.game.winners).toEqual([0]);
  });

  it("a player who can't lose doesn't, unless they concede", () => {
    const board = new Board();
    player(board.game, 1).cannotLose = true;
    playerLoses(board.game, 1, LossReason.LIFE);
    expect(player(board.game, 1).hasLost).toBe(false);
    playerLoses(board.game, 1, LossReason.CONCEDE);
    expect(player(board.game, 1).hasLost).toBe(true);
  });

  it("a player who wins makes everyone else lose (CR 104.2b)", () => {
    const board = new Board();
    playerWins(board.game, 1);
    expect(player(board.game, 0).hasLost).toBe(true);
    expect(board.game.winners).toEqual([1]);
  });
});

describe("durations (CR 611.2b)", () => {
  function effect(duration: number, controller: number, createdTurn: number): ContinuousEffect {
    return { effect: {}, source: 0, controller, timestamp: 0, layer: 0, duration, createdTurn, ability: null, fromStaticAbility: false, isCda: false, expired: false };
  }

  it("each duration ends at its moment", () => {
    const board = new Board();
    const game = board.game;
    game.turn = 3;
    game.activePlayer = 0;
    const eot = effect(Duration.END_OF_TURN, 0, 3);
    const eoc = effect(Duration.END_OF_COMBAT, 0, 3);
    const nextTurn = effect(Duration.YOUR_NEXT_TURN, 0, 3);
    const endOfNext = effect(Duration.END_OF_YOUR_NEXT_TURN, 0, 3);
    game.continuousEffects = [eot, eoc, nextTurn, endOfNext];
    expireAtEndOfCombat(game);
    expect(game.continuousEffects).toEqual([eot, nextTurn, endOfNext]);
    expireAtCleanup(game);
    expect(game.continuousEffects).toEqual([nextTurn, endOfNext]);
    game.turn = 4;
    game.activePlayer = 1;
    expireAtStartOfTurn(game);
    expireAtCleanup(game);
    expect(game.continuousEffects).toEqual([nextTurn, endOfNext]);
    game.turn = 5;
    game.activePlayer = 0;
    expireAtStartOfTurn(game);
    expect(game.continuousEffects).toEqual([endOfNext]);
    expireAtCleanup(game);
    expect(game.continuousEffects).toEqual([]);
  });
});
