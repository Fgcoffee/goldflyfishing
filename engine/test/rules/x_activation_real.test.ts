// Ported from tests/rules/test_cr107_x_in_activation_costs.py, on real cards
// (schema/fixtures): Tezzeret the Seeker's -X removes X loyalty and searches
// for an artifact of mana value X or less (CR 107.3, 606.6). Python uses Mind
// Stone and Wurmcoil Engine with X = 3; Sol Ring and Rakdos Signet stand in
// with X = 1. The Chandra Nalaar and Helix Pinnacle cases wait on fixtures.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, permanents } from "../../src/kernel/game";
import { counterCount, type GameObject } from "../../src/kernel/gameobject";
import { action, ActionKind, perform } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { realCard, RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
function tezzeret(b: RealBoard, loyalty: number): { walker: GameObject; index: number } {
  const walker = b.play("Tezzeret the Seeker");
  walker.counters.set("loyalty", loyalty);
  b.game.epoch += 1;
  const index = b.chars(walker).abilities.findIndex((a) => (a.text ?? "").startsWith("-X"));
  expect(index).toBeGreaterThanOrEqual(0);
  return { walker, index };
}
const inLibrary = (b: RealBoard, name: string): GameObject => createObject(b.game, realCard(name), 0, Zone.LIBRARY);
const onBattlefield = (b: RealBoard, name: string): boolean => permanents(b.game).some((o) => b.chars(o).name === name);

describe("-X in a loyalty cost (CR 107.3, 606.6)", () => {
  it("removes X loyalty and searches for mana value X or less; X was that activation's alone", () => {
    const b = board();
    const { walker, index } = tezzeret(b, 5);
    const big = inLibrary(b, "Rakdos Signet");
    inLibrary(b, "Sol Ring");
    expect(perform(b.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: walker.id, abilityIndex: index, xValue: 1 }))).toBe(true);
    expect(counterCount(walker, "loyalty")).toBe(4);
    resolveTop(b.game);
    expect(onBattlefield(b, "Sol Ring")).toBe(true);
    expect(onBattlefield(b, "Rakdos Signet")).toBe(false);
    expect(big.zone).toBe(Zone.LIBRARY);
    expect(walker.xValue).toBe(0); // CR 107.3k
  });

  it("can't remove more loyalty than the permanent has (CR 606.6)", () => {
    const b = board();
    const { walker, index } = tezzeret(b, 2);
    inLibrary(b, "Sol Ring");
    expect(perform(b.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: walker.id, abilityIndex: index, xValue: 3 }))).toBe(false);
    expect(counterCount(walker, "loyalty")).toBe(2);
    expect(b.game.stack).toHaveLength(0);
  });

  it("X of zero is offered (CR 107.3a)", () => {
    const b = board();
    const { walker, index } = tezzeret(b, 1);
    expect(legalActions(b.game, 0).some((a) => a.source === walker.id && a.abilityIndex === index)).toBe(true);
  });
});
