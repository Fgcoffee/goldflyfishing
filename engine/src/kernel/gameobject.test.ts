// Ported from mtgfish/rules/kernel/gameobject.py.
import { describe, expect, it } from "vitest";
import { Zone } from "./enums";
import {
  addCounters,
  ANY_COUNTER,
  cloneGameObject,
  counterCount,
  isLive,
  isPermanent,
  isSpell,
  newGameObject,
  ObjectKind,
  objectText,
  removeCounters,
} from "./gameobject";

describe("game objects", () => {
  it("counts, adds and removes counters", () => {
    const obj = newGameObject(1, { zone: Zone.BATTLEFIELD });
    expect(addCounters(obj, "+1/+1", 2)).toBe(2);
    expect(addCounters(obj, "+1/+1", 0)).toBe(0);
    addCounters(obj, "lore", 1);
    expect(counterCount(obj, ANY_COUNTER)).toBe(3);
    expect(removeCounters(obj, "+1/+1", 5)).toBe(2);
    expect(obj.counters.has("+1/+1")).toBe(false);
    expect(removeCounters(obj, "lore", -1)).toBe(0);
  });

  it("knows a husk from the live object (CR 400.7)", () => {
    const obj = newGameObject(1, { zone: Zone.BATTLEFIELD });
    expect(isPermanent(obj)).toBe(true);
    obj.supersededBy = 2;
    expect(isLive(obj)).toBe(false);
    expect(isPermanent(obj)).toBe(false);
    expect(obj.zone).toBe(Zone.BATTLEFIELD);
  });

  it("a spell is a card or copy on the stack, not an ability (CR 112.1)", () => {
    expect(isSpell(newGameObject(1, { zone: Zone.STACK }))).toBe(true);
    expect(isSpell(newGameObject(2, { zone: Zone.STACK, kind: ObjectKind.COPY }))).toBe(true);
    expect(isSpell(newGameObject(3, { zone: Zone.STACK, kind: ObjectKind.ABILITY }))).toBe(false);
  });

  it("a clone shares nothing mutable with the original", () => {
    const obj = newGameObject(1, { zone: Zone.BATTLEFIELD, targets: [[5]] });
    addCounters(obj, "+1/+1", 1);
    obj.goadedBy.add(0);
    const copy = cloneGameObject(obj);
    addCounters(copy, "+1/+1", 1);
    copy.goadedBy.add(1);
    copy.targets[0]?.push(6);
    copy.tapped = true;
    expect(counterCount(obj, "+1/+1")).toBe(1);
    expect([...obj.goadedBy]).toEqual([0]);
    expect(obj.targets).toEqual([[5]]);
    expect(obj.tapped).toBe(false);
  });

  it("describes itself for the log", () => {
    const obj = newGameObject(12, { tapped: true });
    addCounters(obj, "+1/+1", 1);
    expect(objectText(obj, "Grizzly Bears")).toBe("#12 Grizzly Bears (tapped) (1 +1/+1)");
  });
});
