// Ported from the state and queries of mtgfish/rules/kernel/game.py.
import { describe, expect, it } from "vitest";
import { newPlayer } from "../rules/cr100_game_concepts/player";
import { Zone } from "./enums";
import { addCounters, counterCount } from "./gameobject";
import {
  agentFor,
  apnapOrder,
  cloneGame,
  controlPlayer,
  createObject,
  logRecord,
  newGame,
  nextPlayer,
  objectsIn,
  permanents,
  player,
  turnOrderFrom,
  zoneList,
} from "./game";
import { logDigest } from "./log";
import { nextUint32 } from "./random";

function fourPlayers(seed = 1) {
  return newGame([0, 1, 2, 3].map((id) => newPlayer(id, { name: `P${id}` })), { seed });
}

describe("game state", () => {
  it("orders players APNAP and skips those who left, keeping seats (CR 101.4, 800.4)", () => {
    const game = fourPlayers();
    game.activePlayer = 2;
    expect(apnapOrder(game)).toEqual([2, 3, 0, 1]);
    player(game, 3).hasLost = true;
    expect(apnapOrder(game)).toEqual([2, 0, 1]);
    expect(nextPlayer(game, 2)).toBe(0);
    expect(turnOrderFrom(game, 9)).toEqual([0, 1, 2]);
  });

  it("creates objects in shared and owned zones", () => {
    const game = fourPlayers();
    const a = createObject(game, { name: "A" }, 0, Zone.LIBRARY);
    const b = createObject(game, { name: "B" }, 0, Zone.LIBRARY, { toTop: true });
    const c = createObject(game, { name: "C" }, 1, Zone.BATTLEFIELD);
    expect(zoneList(game, Zone.LIBRARY, 0)).toEqual([b.id, a.id]);
    expect(objectsIn(game, Zone.BATTLEFIELD).map((o) => o.id)).toEqual([c.id]);
    expect(new Set([a.id, b.id, c.id]).size).toBe(3);
    expect(b.timestamp).toBeGreaterThan(a.timestamp);
    expect(() => zoneList(game, Zone.HAND)).toThrow();
  });

  it("phased-out permanents don't count as permanents (CR 702.26a)", () => {
    const game = fourPlayers();
    const a = createObject(game, null, 0, Zone.BATTLEFIELD);
    const b = createObject(game, null, 1, Zone.BATTLEFIELD);
    b.phasedOut = true;
    expect(permanents(game).map((o) => o.id)).toEqual([a.id]);
    expect(permanents(game, 1)).toEqual([]);
  });

  it("a controlled player's decisions go to their controller (CR 723.5)", () => {
    const game = fourPlayers();
    game.agents.set(0, "agent0");
    game.agents.set(1, "agent1");
    expect(agentFor(game, 1)).toBe("agent1");
    controlPlayer(game, 1, 0);
    expect(agentFor(game, 1)).toBe("agent0");
  });

  it("a clone can be played out without touching the real game", () => {
    const game = fourPlayers(42);
    const bear = createObject(game, { name: "Bear" }, 0, Zone.BATTLEFIELD);
    createObject(game, null, 0, Zone.HAND);
    logRecord(game, "setup");
    const digest = logDigest(game.log);
    const nextRandom = nextUint32(cloneGame(game).rng);

    const shadow = cloneGame(game);
    const shadowBear = shadow.objects.get(bear.id);
    if (shadowBear === undefined) throw new Error("missing");
    shadowBear.tapped = true;
    addCounters(shadowBear, "+1/+1", 1);
    player(shadow, 0).hand.length = 0;
    player(shadow, 0).life = 1;
    createObject(shadow, null, 0, Zone.BATTLEFIELD);
    logRecord(shadow, "planning");
    for (let i = 0; i < 10; i += 1) nextUint32(shadow.rng);

    expect(bear.tapped).toBe(false);
    expect(counterCount(bear, "+1/+1")).toBe(0);
    expect(player(game, 0).hand).toHaveLength(1);
    expect(player(game, 0).life).toBe(40);
    expect(game.battlefield).toEqual([bear.id]);
    expect(logDigest(game.log)).toBe(digest);
    expect(nextUint32(game.rng)).toBe(nextRandom);
    // Object ids keep counting from where the real game was.
    expect(createObject(game, null, 0, Zone.EXILE).id).toBe(shadow.ids.nextObject - 1);
  });
});
