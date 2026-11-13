// End to end: agents that play lands and cast creatures, through legal
// actions, casting, paying and resolving the stack. Not a Python port; a
// smoke test that the pieces meet, and that a game replays to the same digest.
import { describe, expect, it } from "vitest";
import { Zone } from "../../src/kernel/enums";
import { createObject, type Game } from "../../src/kernel/game";
import { logDigest } from "../../src/kernel/log";
import { ActionKind, PassingAgent, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { DEFAULT_TURN_OPTIONS, runGame } from "../../src/rules/cr500_turn_structure/turn";
import { Board, CARDS } from "./board";

/** Plays a land or casts a spell whenever it can; never taps for mana by hand. */
const eager = (): PassingAgent =>
  Object.assign(new PassingAgent(), {
    chooseAction: (_game: Game, _player: number, legal: readonly Action[]): Action =>
      legal.find((a) => a.kind === ActionKind.PLAY_LAND) ?? legal.find((a) => a.kind === ActionKind.CAST_SPELL) ?? legal[0]!,
  });

function play(seed: number): Game {
  const board = new Board();
  const game = board.game;
  game.log.enabled = true;
  for (const p of game.players) {
    game.agents.set(p.id, eager());
    for (let i = 0; i < 30; i += 1) createObject(game, CARDS[i % 3 === 0 ? "Grizzly Bears" : "Forest"]!, p.id, Zone.LIBRARY);
  }
  game.activePlayer = 0;
  void seed;
  runGame(game, { ...DEFAULT_TURN_OPTIONS, maxRounds: 8 });
  return game;
}

describe("a game where spells are cast and resolve", () => {
  it("lands are played and creatures resolve onto the battlefield", () => {
    const game = play(1);
    const names = game.battlefield.map((id) => characteristicsOf(game, game.objects.get(id)!).name);
    expect(names.filter((n) => n === "Forest").length).toBeGreaterThan(2);
    expect(names.filter((n) => n === "Grizzly Bears").length).toBeGreaterThan(0);
    expect(game.stack).toHaveLength(0);
  });

  it("replays to the same digest", () => {
    expect(logDigest(play(1).log)).toBe(logDigest(play(1).log));
  });
});
