// Ported from tests/rules/test_loops.py: an infinite loop ends the game it is
// in, instead of the measurement (CR 732.2a, 104.4b). Real boards through the
// real priority loop (schema/fixtures), synthetic cycles fed straight to the
// detector, and the arithmetic of `decide`. The Sandbox's PassiveOpponent is
// PassingAgent here.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { cloneGame, createObject, invalidateAllCharacteristics, livingPlayers, player, type Game } from "../../src/kernel/game";
import { ObjectKind } from "../../src/kernel/gameobject";
import { gainLife, destroy } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind, poolTotal } from "../../src/rules/cr100_game_concepts/mana_pool";
import { loseLife } from "../../src/rules/cr100_game_concepts/player";
import { PassingAgent, runPriority, settle, WORK_BUDGET } from "../../src/rules/cr100_game_concepts/priority";
import {
  beginWindow,
  decide,
  DECLINED,
  LOOP_REPETITIONS,
  LOST,
  MANA_CAP,
  NOTHING,
  noteStep,
  WINS,
  type Contestant,
  type Values,
} from "../../src/rules/loops";
import { moveObject } from "../../src/rules/zones";
import { SimpleAgent } from "../../src/ai/simple";
import { realCard, RealBoard } from "./real_cards";

const ENGINE = JSON.stringify(["A", 0, "ACTIVATE_ABILITY", "Some Engine", 0, 0, []]);
const TRIGGER = JSON.stringify(["R", 0, "Whenever something happens, nothing does.", []]);

function box(players = 2): RealBoard {
  const b = new RealBoard(players);
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, new PassingAgent());
  return b;
}

/** Feed one step per cycle, changing the game each time, until the detector acts; how many cycles ran, or null. */
function spin(game: Game, step: string, change: (g: Game) => void, limit = 60): number | null {
  beginWindow(game);
  for (let cycle = 1; cycle <= limit; cycle += 1) {
    change(game);
    if (noteStep(game, step)) return cycle;
  }
  return null;
}

const lose = (game: Game, pid: number, n: number): void => void loseLife(player(game, pid), n);

describe("real boards", () => {
  it("Sanguine Bond and Exquisite Blood end the game at exact life totals", () => {
    const b = box();
    b.play("Sanguine Bond");
    b.play("Exquisite Blood");
    gainLife(b.game, 0, 1);
    runPriority(b.game);
    expect(b.game.gameOver).toBe(true);
    expect(b.game.winners).toEqual([0]);
    expect(player(b.game, 1).hasLost).toBe(true);
    expect(b.game.runaway).toBe("");
    expect(player(b.game, 0).life).toBe(81);
    expect(b.game.loops).toHaveLength(1);
    expect(b.game.loops[0]!.startsWith("shortcut")).toBe(true);
  });

  it("a board wipe under an aristocrat is not a loop (Zulaport Cutthroat, 30 tokens)", () => {
    const b = box();
    player(b.game, 1).life = 35;
    b.play("Zulaport Cutthroat");
    const tokens = Array.from({ length: 30 }, () => createObject(b.game, realCard("Grizzly Bears"), 0, Zone.BATTLEFIELD, { kind: ObjectKind.TOKEN }));
    invalidateAllCharacteristics(b.game);
    for (const token of tokens) destroy(b.game, token);
    runPriority(b.game);
    expect(b.game.loops).toEqual([]);
    expect(player(b.game, 1).life).toBe(5);
    expect(player(b.game, 0).life).toBe(70);
    expect(b.game.gameOver).toBe(false);
  });

  it("the bot's activation cap does not beat the detector", () => {
    expect(SimpleAgent.MAX_ACTIVATIONS_PER_TURN).toBeGreaterThan(LOOP_REPETITIONS);
  });

  it("Sanguine Bond finishes a four-player table: 40 + 1 + 120", () => {
    const b = box(4);
    b.play("Sanguine Bond");
    b.play("Exquisite Blood");
    gainLife(b.game, 0, 1);
    runPriority(b.game);
    expect(b.game.gameOver).toBe(true);
    expect(b.game.winners).toEqual([0]);
    expect([1, 2, 3].every((i) => player(b.game, i).hasLost)).toBe(true);
    expect(b.game.runaway).toBe("");
    expect(player(b.game, 0).life).toBe(161);
  });
});

describe("synthetic cycles", () => {
  it("an optional drain loop is run to the kill, and may carry on", () => {
    const b = box();
    const ran = spin(b.game, ENGINE, (g) => lose(g, 1, 2));
    expect(ran).not.toBeNull();
    expect(ran!).toBeGreaterThanOrEqual(LOOP_REPETITIONS);
    runPriority(b.game);
    expect(player(b.game, 1).hasLost).toBe(true);
    expect(player(b.game, 0).life).toBe(40);
    expect(b.game.loops[0]!.startsWith("shortcut")).toBe(true);
    expect(b.game.continuingLoopSteps.has(ENGINE)).toBe(true);
  });

  it("a pinger does exactly forty per player, then moves on", () => {
    const b = box(4);
    beginWindow(b.game);
    for (let i = 0; i < 400; i += 1) {
      const living = livingPlayers(b.game).filter((p) => p.id !== 0);
      if (living.length === 0 || b.game.gameOver) break;
      const target = living[0]!;
      const step = JSON.stringify(["A", 0, "ACTIVATE_ABILITY", "Pinger", 0, 0, [["p", target.id]]]);
      loseLife(target, 1);
      noteStep(b.game, step);
      settle(b.game);
    }
    expect(b.game.gameOver).toBe(true);
    expect(b.game.winners).toEqual([0]);
    expect(b.game.players.slice(1).map((p) => p.life)).toEqual([0, 0, 0]);
    expect(b.game.loops.some((e) => e.startsWith("shortcut"))).toBe(true);
  });

  it("a loop that was answered can carry on", () => {
    const b = box();
    expect(spin(b.game, ENGINE, (g) => lose(g, 1, 1))).not.toBeNull();
    expect(player(b.game, 1).life).toBe(0);
    player(b.game, 1).life = 10; // a response before state-based actions
    expect(b.game.exhaustedLoopSteps.has(ENGINE)).toBe(false);
    expect(spin(b.game, ENGINE, (g) => lose(g, 1, 1))).not.toBeNull();
    expect(player(b.game, 1).life).toBe(0);
    runPriority(b.game);
    expect(player(b.game, 1).hasLost).toBe(true);
  });

  it("an optional loop stops where its fuel does: 20 cards pay for 20 drains", () => {
    const b = box();
    for (let i = 0; i < 20; i += 1) createObject(b.game, realCard("Forest"), 0, Zone.LIBRARY);
    const millToDrain = (g: Game): void => {
      const me = player(g, 0);
      moveObject(g, g.objects.get(me.library[me.library.length - 1]!)!, Zone.GRAVEYARD, { toPlayer: 0 });
      lose(g, 1, 1);
    };
    expect(spin(b.game, ENGINE, millToDrain)).not.toBeNull();
    expect(player(b.game, 0).library).toHaveLength(0);
    expect(player(b.game, 0).graveyard).toHaveLength(20);
    expect(player(b.game, 1).life).toBe(20);
    expect(player(b.game, 1).hasLost).toBe(false);
    expect(b.game.loops[0]!.startsWith("shortcut")).toBe(true);
    expect(b.game.exhaustedLoopSteps.has(ENGINE)).toBe(true);
  });

  it("an optional loop that would kill its controller first is declined", () => {
    const b = box();
    player(b.game, 0).life = 20;
    expect(spin(b.game, ENGINE, (g) => (lose(g, 0, 1), lose(g, 1, 1)))).not.toBeNull();
    expect(b.game.loops[0]!.startsWith("declined")).toBe(true);
    expect(player(b.game, 0).life).toBeGreaterThan(0);
    expect(b.game.gameOver).toBe(false);
    expect(b.game.exhaustedLoopSteps.has(ENGINE)).toBe(true);
  });

  it("an optional loop that changes nothing is stopped and recorded", () => {
    const b = box();
    expect(spin(b.game, ENGINE, () => {})).not.toBeNull();
    expect(b.game.loops[0]!.startsWith("stopped")).toBe(true);
    expect(b.game.players.map((p) => p.life)).toEqual([40, 40]);
    expect(b.game.exhaustedLoopSteps.has(ENGINE)).toBe(true);
  });

  it("a mana loop is run to a generous cap", () => {
    const b = box();
    const green = manaKind(Color.GREEN);
    expect(spin(b.game, ENGINE, (g) => addMana(player(g, 0).manaPool, green, 1))).not.toBeNull();
    expect(poolTotal(player(b.game, 0).manaPool)).toBeGreaterThanOrEqual(MANA_CAP);
    expect(b.game.loops[0]!.startsWith("shortcut")).toBe(true);
    expect(b.game.gameOver).toBe(false);
  });

  it("a mandatory loop nothing ends is a draw (CR 104.4b), not a stall-out", () => {
    const b = box();
    expect(spin(b.game, TRIGGER, () => {})).not.toBeNull();
    expect(b.game.gameOver).toBe(true);
    expect(b.game.winners).toEqual([]);
    expect(b.game.loopDraw).toBe(true);
    expect(b.game.loops[0]!.startsWith("draw")).toBe(true);
  });

  it("a repeat that stops short of eight cycles is left alone", () => {
    const b = box();
    expect(spin(b.game, ENGINE, (g) => lose(g, 1, 1), LOOP_REPETITIONS - 1)).toBeNull();
    expect(player(b.game, 1).life).toBe(40 - (LOOP_REPETITIONS - 1));
    expect(b.game.loops).toEqual([]);
  });

  it("an uneven cycle is not a loop", () => {
    const b = box();
    let n = 0;
    expect(spin(b.game, ENGINE, (g) => lose(g, 1, (n += 1) % 3), 30)).toBeNull();
    expect(b.game.loops).toEqual([]);
  });
});

describe("the arithmetic of decide", () => {
  const players = (...lives: number[]): Contestant[] => lives.map((life, id) => ({ id, life }));
  const delta = (entries: [unknown[], number][]): Values => new Map(entries.map(([k, v]) => [JSON.stringify(k), v]));

  it("a mandatory loop runs until the first loss", () => {
    const d = decide({ optional: false, controller: 0, delta: delta([[["life", 1], -2]]), values: new Map(), players: players(40, 10) });
    expect([d.cycles, d.reason]).toEqual([5, LOST]);
  });

  it("an optional loop kills every opponent it can reach", () => {
    const d = decide({
      optional: true, controller: 0,
      delta: delta([[["life", 1], -1], [["life", 2], -1], [["life", 3], -1]]),
      values: new Map(), players: players(40, 10, 30, 25),
    });
    expect([d.cycles, d.reason]).toEqual([30, WINS]);
  });

  it("poison counts to ten", () => {
    const d = decide({ optional: true, controller: 0, delta: delta([[["poison", 1], 1]]), values: new Map(), players: [{ id: 0, life: 40 }, { id: 1, life: 40, poison: 3 }] });
    expect([d.cycles, d.reason]).toEqual([7, WINS]);
  });

  it("a draw loop leaves its controller a card", () => {
    const d = decide({
      optional: true, controller: 0,
      delta: delta([[["library", 0], -1], [["hand", 0], 1], [["life", 1], -1]]),
      values: delta([[["library", 0], 10]]),
      players: [{ id: 0, life: 40, library: 10 }, { id: 1, life: 40 }],
    });
    expect(d.cycles).toBe(9);
  });

  it("an opponent who cannot lose is not a goal", () => {
    const d = decide({ optional: true, controller: 0, delta: delta([[["life", 1], -1]]), values: new Map(), players: [{ id: 0, life: 40 }, { id: 1, life: 5, cannotLose: true }] });
    expect([d.outcome, d.reason]).toEqual(["stopped", NOTHING]);
  });

  it("declining is a decision, not an error", () => {
    const d = decide({ optional: true, controller: 0, delta: delta([[["life", 0], -1], [["life", 1], -1]]), values: new Map(), players: players(20, 40) });
    expect([d.cycles, d.outcome, d.reason]).toEqual([0, "declined", DECLINED]);
  });
});

describe("the work budget (not a rule: no game runs for minutes)", () => {
  it("a game past WORK_BUDGET stops as a runaway, deterministically", () => {
    const b = box();
    b.game.work.units = WORK_BUDGET + 1;
    runPriority(b.game);
    expect(b.game.gameOver).toBe(true);
    expect(b.game.runaway).toBe("WORK BUDGET no stack");
  });
  it("a clone shares the meter: the bot's lookahead counts", () => {
    const b = box();
    const clone = cloneGame(b.game);
    clone.work.units += 5;
    expect(b.game.work.units).toBe(5);
  });
});

