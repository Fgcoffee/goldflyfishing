// Ported from tests/rules/test_cr120_excess_damage.py: excess damage (CR 120.10),
// measured, and carried on the damage event.
import { describe, expect, it } from "vitest";
import { CardType } from "../../src/kernel/enums";
import { EventKind } from "../../src/kernel/events";
import { addCounters } from "../../src/kernel/gameobject";
import { dealDamage, excessDamage } from "../../src/rules/cr100_game_concepts/actions";
import { Board, CARDS, cardDef } from "./board";

CARDS["Walker"] = cardDef("Walker", { types: CardType.PLANESWALKER });
CARDS["Battle"] = cardDef("Battle", { types: CardType.BATTLE });

describe("excess damage (CR 120.10)", () => {
  it("none short of lethal or at exactly lethal; the amount over lethal past it", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    expect(excessDamage(board.game, bears, 1)).toBe(0);
    expect(excessDamage(board.game, bears, 2)).toBe(0);
    expect(excessDamage(board.game, bears, 5)).toBe(3);
  });

  it("damage already marked makes excess come sooner", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    bears.damage = 1;
    expect(excessDamage(board.game, bears, 3)).toBe(2);
  });

  it("a planeswalker measures against its loyalty, a battle against its defense", () => {
    const board = new Board();
    const walker = board.play("Walker");
    addCounters(walker, "loyalty", 4);
    expect(excessDamage(board.game, walker, 4)).toBe(0);
    expect(excessDamage(board.game, walker, 6)).toBe(2);
    const battle = board.play("Battle");
    addCounters(battle, "defense", 3);
    expect(excessDamage(board.game, battle, 4)).toBe(1);
  });

  it("a permanent that takes no damage has no excess (CR 120.3)", () => {
    const board = new Board();
    expect(excessDamage(board.game, board.play("Forest"), 5)).toBe(0);
  });

  it("the damage event carries the excess, zero when there is none", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const seen: number[] = [];
    board.game.observer = (_g, e) => {
      if (e.kind === EventKind.DAMAGE_DEALT || e.kind === EventKind.COMBAT_DAMAGE_DEALT) seen.push((e.data[0] as number | undefined) ?? 0);
    };
    dealDamage(board.game, board.play("Grizzly Bears"), 5);
    expect(seen.at(-1)).toBe(3);
    dealDamage(board.game, board.play("Grizzly Bears"), 1);
    expect(seen.at(-1)).toBe(0);
  });
});
