// F65 (auditor, 15 Nov): a husk that ceased to exist points `supersededBy` at
// itself (a token after CR 704.5d with its death trigger pending; a completed
// dungeon). resolve.ts followed the link with `while (obj.supersededBy)`, so a
// remembered id that reached such a husk never finished: the user's browser
// runs hung on about 1 game in 60 (Vraska, Muddle and Kilo decks). Python has
// the same loop.
import { describe, expect, it } from "vitest";
import { latestObject, newGameObject, type GameObject } from "../../src/kernel/gameobject";
import type { ObjectId } from "../../src/kernel/ids";

function objects(...objs: GameObject[]): Map<ObjectId, GameObject> {
  return new Map(objs.map((o) => [o.id, o]));
}

describe("F65: following an object to what it became never hangs", () => {
  it("a live object is itself", () => {
    const map = objects(newGameObject(1));
    expect(latestObject(map, 1)?.id).toBe(1);
  });

  it("a moved object is the object it became (CR 400.7)", () => {
    const map = objects(newGameObject(1, { supersededBy: 2 }), newGameObject(2, { supersededBy: 3 }), newGameObject(3));
    expect(latestObject(map, 1)?.id).toBe(3);
  });

  it("a husk pointing at itself has ceased to exist: undefined, not a hang", () => {
    const map = objects(newGameObject(1, { supersededBy: 2 }), newGameObject(2, { supersededBy: 2 }));
    expect(latestObject(map, 1)).toBeUndefined();
    expect(latestObject(map, 2)).toBeUndefined();
  });

  it("a cycle is treated the same", () => {
    const map = objects(newGameObject(1, { supersededBy: 2 }), newGameObject(2, { supersededBy: 1 }));
    expect(latestObject(map, 1)).toBeUndefined();
  });

  it("an id that no longer exists is undefined", () => {
    expect(latestObject(objects(newGameObject(1, { supersededBy: 9 })), 1)).toBeUndefined();
    expect(latestObject(objects(), 5)).toBeUndefined();
  });
});
