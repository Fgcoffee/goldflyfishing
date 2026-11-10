// Ported from mtgfish/rules/cr100_game_concepts/player.py.
import { describe, expect, it } from "vitest";
import { Zone } from "../../kernel/enums";
import { addMana, manaKind, poolTotal } from "./mana_pool";
import {
  addPlayerCounters,
  clonePlayer,
  commanderTax,
  gainLife,
  increaseSpeed,
  lethalCommanderDamage,
  loseLife,
  newPlayer,
  playerCounterCount,
  playerZone,
  recordCommanderCast,
  removePlayerCounters,
  startEngines,
  takeCommanderDamage,
} from "./player";
import { Color } from "../../kernel/enums";

describe("players", () => {
  it("start at 40 life (CR 903.7)", () => {
    expect(newPlayer(0).life).toBe(40);
  });

  it("gain and lose only positive amounts (CR 119.9, 107.1b)", () => {
    const p = newPlayer(0);
    expect(gainLife(p, 0)).toBe(0);
    expect(gainLife(p, -3)).toBe(0);
    expect(loseLife(p, -3)).toBe(0);
    expect(gainLife(p, 5)).toBe(5);
    expect(loseLife(p, 2)).toBe(2);
    expect(p.life).toBe(43);
    expect(p.lifeGainedThisTurn).toBe(5);
    expect(p.lifeLostThisTurn).toBe(2);
  });

  it("named and other counters are reached the same way (CR 122.1)", () => {
    const p = newPlayer(0);
    addPlayerCounters(p, "poison", 3);
    addPlayerCounters(p, "ticket", 2);
    expect(p.poison).toBe(3);
    expect(playerCounterCount(p, "ticket")).toBe(2);
    expect(removePlayerCounters(p, "ticket", 5)).toBe(2);
    expect(p.counters.has("ticket")).toBe(false);
  });

  it("commander tax and commander damage (CR 903.8, 903.10a)", () => {
    const p = newPlayer(0);
    expect(commanderTax(p, 7)).toBe(0);
    recordCommanderCast(p, 7);
    recordCommanderCast(p, 7);
    expect(commanderTax(p, 7)).toBe(4);
    takeCommanderDamage(p, 9, 20);
    expect(lethalCommanderDamage(p)).toBe(0);
    takeCommanderDamage(p, 9, 1);
    expect(lethalCommanderDamage(p)).toBe(9);
  });

  it("speed starts once and rises once a turn (CR 702.179)", () => {
    const p = newPlayer(0);
    expect(increaseSpeed(p)).toBe(false);
    expect(startEngines(p)).toBe(true);
    expect(startEngines(p)).toBe(false);
    expect(increaseSpeed(p)).toBe(true);
    expect(increaseSpeed(p)).toBe(false);
    expect(p.speed).toBe(2);
  });

  it("a clone shares no zone or pool with the original", () => {
    const p = newPlayer(0);
    p.hand.push(1);
    const copy = clonePlayer(p);
    playerZone(copy, Zone.HAND).push(2);
    addMana(copy.manaPool, manaKind(Color.BLUE));
    expect(p.hand).toEqual([1]);
    expect(poolTotal(p.manaPool)).toBe(0);
    expect(() => playerZone(p, Zone.EXILE)).toThrow();
  });
});
