/**
 * The simple bot and its lookahead. Ported from tests/sim/test_lookahead.py,
 * tests/sim/test_bot_loops.py, the bot cases of test_player_targets.py, and
 * the bot's combat choices, on real parsed cards (schema/fixtures/).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { RealBoard } from "../../test/rules/real_cards";
import { Phase, Step, Zone } from "../kernel/enums";
import type { Game } from "../kernel/game";
import { logDigest } from "../kernel/log";
import { playerTarget } from "../kernel/ids";
import { newPlayer } from "../rules/cr100_game_concepts/player";
import { action, ActionKind, PASS, type Action } from "../rules/cr100_game_concepts/priority";
import { newCombat } from "../rules/cr500_turn_structure/combat";
import { installReplacements } from "../rules/cr600_spells_and_abilities/replacement";
import { installMatch } from "../rules/match";
import { features, project } from "./lookahead";
import { SimpleAgent } from "./simple";

let board: RealBoard;
const cast = (id: number): Action => action(ActionKind.CAST_SPELL, { source: id });

beforeEach(() => {
  board = new RealBoard();
  installMatch();
  installReplacements();
  const game = board.game;
  game.activePlayer = 0;
  game.priorityPlayer = 0;
  game.phase = Phase.PRECOMBAT_MAIN;
  game.step = Step.MAIN;
  for (const p of game.players) game.agents.set(p.id, new SimpleAgent({ lookahead: false }));
  for (let i = 0; i < 10; i++) {
    const forest = board.hand("Forest");
    game.players[0]!.hand.pop();
    forest.zone = Zone.LIBRARY;
    game.players[0]!.library.push(forest.id);
  }
});

function fingerprint(game: Game): string {
  return JSON.stringify([
    logDigest(game.log),
    game.rng,
    game.players[0]!.library,
    game.players[0]!.hand,
    game.battlefield,
    game.battlefield.map((id) => game.objects.get(id)!.tapped),
    game.players.map((p) => p.life),
    game.objects.size,
  ]);
}

describe("lookahead (test_lookahead.py)", () => {
  it("leaves the real game alone: not a card, a tap, a random number or a line of the log", () => {
    for (let i = 0; i < 3; i++) board.play("Island");
    const divination = board.hand("Divination");
    const before = fingerprint(board.game);
    project(board.game, 0, cast(divination.id));
    expect(fingerprint(board.game)).toBe(before);
  });

  it("a draw is seen as a draw", () => {
    for (let i = 0; i < 3; i++) board.play("Island");
    const projection = project(board.game, 0, cast(board.hand("Divination").id));
    expect(projection.legal && projection.changed).toBe(true);
    expect(projection.players.get(0)!.cardsDrawn).toBe(2);
  });

  it("what a permanent sets off is seen too", () => {
    board.play("Beast Whisperer");
    board.play("Forest");
    board.play("Forest");
    const projection = project(board.game, 0, cast(board.hand("Grizzly Bears").id));
    expect(projection.entered.get(0)).toEqual(["Grizzly Bears"]);
    expect(projection.resolutions).toBeGreaterThanOrEqual(2);
    expect(projection.players.get(0)!.cardsDrawn).toBe(1);
  });

  it("an action the engine would refuse is seen as refused", () => {
    expect(project(board.game, 0, cast(board.hand("Lightning Bolt").id)).legal).toBe(false);
  });

  it("features have a fixed shape", () => {
    for (let i = 0; i < 3; i++) board.play("Island");
    const drawn = features(project(board.game, 0, cast(board.hand("Divination").id)), 0);
    const refused = features(project(board.game, 0, cast(board.hand("Lightning Bolt").id)), 0);
    expect(drawn.length).toBe(refused.length);
  });

  it("the bot passes over a spell the engine would refuse", () => {
    const bot = new SimpleAgent();
    const bolt = board.hand("Lightning Bolt");
    expect(bot.chooseAction(board.game, 0, [cast(bolt.id)])).toBe(PASS);
  });
});

describe("the bot's memory (test_bot_loops.py)", () => {
  it("a failed action isn't retried in the same step, and is forgotten when the step moves on", () => {
    const bot = new SimpleAgent({ lookahead: false });
    const forest = board.play("Forest");
    const tried = action(ActionKind.ACTIVATE_ABILITY, { source: forest.id, abilityIndex: 5 });
    bot.actionFailed(board.game, 0, tried);
    expect(bot.chooseAction(board.game, 0, [tried])).toBe(PASS);
    board.game.step = Step.END_STEP;
    board.game.activePlayer = 0;
    expect(bot.chooseAction(board.game, 0, [tried])).toBe(tried);
  });

  it("the same card is cast at most so many times a turn", () => {
    const bot = new SimpleAgent({ lookahead: false });
    const bears = board.hand("Grizzly Bears");
    for (let i = 0; i < SimpleAgent.MAX_CASTS_PER_TURN; i++) expect(bot.chooseAction(board.game, 0, [cast(bears.id)]).kind).toBe(ActionKind.CAST_SPELL);
    expect(bot.chooseAction(board.game, 0, [cast(bears.id)])).toBe(PASS);
    board.game.turn += 1;
    expect(bot.chooseAction(board.game, 0, [cast(bears.id)]).kind).toBe(ActionKind.CAST_SPELL);
  });

  it("prefers a land, then the biggest creature", () => {
    const bot = new SimpleAgent({ lookahead: false });
    const forest = board.hand("Forest");
    const bears = board.hand("Grizzly Bears");
    const elves = board.hand("Llanowar Elves");
    const land = action(ActionKind.PLAY_LAND, { source: forest.id });
    expect(bot.chooseAction(board.game, 0, [cast(elves.id), land, cast(bears.id)])).toBe(land);
    expect(bot.chooseAction(board.game, 0, [cast(elves.id), cast(bears.id)]).source).toBe(bears.id);
  });

  it("holds an instant on an opponent's turn, but spends it in the end step before its own", () => {
    const bot = new SimpleAgent({ lookahead: false });
    const bolt = board.hand("Lightning Bolt");
    board.game.activePlayer = 1;
    board.game.phase = Phase.BEGINNING;
    board.game.step = Step.UPKEEP;
    expect(bot.chooseAction(board.game, 0, [cast(bolt.id)])).toBe(PASS);
    board.game.phase = Phase.ENDING;
    board.game.step = Step.END_STEP;
    expect(bot.chooseAction(board.game, 0, [cast(bolt.id)]).kind).toBe(ActionKind.CAST_SPELL);
  });
});

describe("targets (test_player_targets.py)", () => {
  it("aims harm at the weakest opponent and help at itself", () => {
    board.game.players.push(newPlayer(2), newPlayer(3));
    board.game.turnOrder = [0, 1, 2, 3];
    board.game.players[2]!.life = 7;
    const bot = new SimpleAgent();
    const shock = board.hand("Shock");
    const everyone = [0, 1, 2, 3].map(playerTarget);
    expect(bot.chooseTargets(board.game, 0, shock.id, [everyone])).toEqual([[playerTarget(2)]]);
  });

  it("aims removal at an opponent's biggest creature, not its own", () => {
    const mine = board.play("Grizzly Bears", 0);
    const theirs = board.play("Grizzly Bears", 1);
    const shock = board.hand("Shock");
    expect(new SimpleAgent().chooseTargets(board.game, 0, shock.id, [[mine.id, theirs.id]])).toEqual([[theirs.id]]);
  });
});

describe("combat", () => {
  it("attacks the opponent closest to losing with everything that has power", () => {
    const bears = board.play("Grizzly Bears", 0);
    board.game.players.push(newPlayer(2));
    board.game.turnOrder = [0, 1, 2];
    board.game.players[2]!.life = 5;
    const attacks = new SimpleAgent().declareAttackers(board.game, 0, [bears]);
    expect([...attacks]).toEqual([[bears.id, 2]]);
  });

  it("blocks what would kill it; otherwise only good blocks", () => {
    const attacker = board.play("Grizzly Bears", 1);
    const elves = board.play("Llanowar Elves", 0);
    const combat = newCombat();
    combat.attacking.set(attacker.id, 0);
    const bot = new SimpleAgent();
    expect(bot.declareBlockers(board.game, 0, combat, [elves]).size).toBe(0); // an Elf neither survives nor kills a Bear
    board.game.players[0]!.life = 2;
    expect([...bot.declareBlockers(board.game, 0, combat, [elves])]).toEqual([[elves.id, [attacker.id]]]);
  });
});
