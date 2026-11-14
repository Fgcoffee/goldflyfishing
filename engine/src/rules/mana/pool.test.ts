// Ported from ManaPool / ManaKind in mtgfish/rules/cr100_game_concepts/cr106_mana.py.
import { describe, expect, it } from "vitest";
import { Color } from "../../kernel/enums";
import {
  addMana,
  amountOfColor,
  clearPool,
  cloneManaPool,
  manaKind,
  manaOfKind,
  newManaPool,
  poolText,
  poolTotal,
  removeMana,
} from "./pool";

describe("mana pool (CR 106, 500.4)", () => {
  it("buckets equal kinds together, by value not identity", () => {
    const pool = newManaPool();
    addMana(pool, manaKind(Color.GREEN), 2);
    addMana(pool, manaKind(Color.GREEN), 1);
    addMana(pool, manaKind(Color.GREEN, { snow: true }));
    expect(pool.buckets.size).toBe(2);
    expect(manaOfKind(pool, manaKind(Color.GREEN))).toBe(3);
    expect(poolTotal(pool)).toBe(4);
    expect(amountOfColor(pool, Color.GREEN)).toBe(4);
    addMana(pool, manaKind(Color.RED), 0);
    expect(pool.buckets.size).toBe(2);
  });

  it("refuses to remove mana it does not hold", () => {
    const pool = newManaPool();
    addMana(pool, manaKind(Color.NONE), 1);
    expect(() => removeMana(pool, manaKind(Color.NONE), 2)).toThrow();
    removeMana(pool, manaKind(Color.NONE), 1);
    expect(poolTotal(pool)).toBe(0);
  });

  it("empties, keeping until-end-of-combat mana only when asked (CR 702.189a)", () => {
    const pool = newManaPool();
    addMana(pool, manaKind(Color.RED), 2);
    addMana(pool, manaKind(Color.RED, { untilEndOfCombat: true }), 1);
    expect(clearPool(cloneManaPool(pool), true)).toBe(2);
    expect(clearPool(pool)).toBe(3);
    expect(poolTotal(pool)).toBe(0);
  });

  it("prints in a deterministic colour order", () => {
    const pool = newManaPool();
    addMana(pool, manaKind(Color.GREEN));
    addMana(pool, manaKind(Color.WHITE), 2);
    addMana(pool, manaKind(Color.NONE));
    expect(poolText(pool)).toBe("1xC 2xW 1xG");
    expect(poolText(newManaPool())).toBe("(empty)");
  });
});
