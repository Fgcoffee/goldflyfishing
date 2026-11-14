// Ported from mtgfish/rules/kernel/game.py (game.rng) and
// mtgfish/sim/runner.py (RunConfig.seed_for).
import { describe, expect, it } from "vitest";
import {
  choice,
  cloneRng,
  newRng,
  nextUint32,
  randint,
  random,
  randrange,
  seedFor,
  shuffle,
} from "./random";

describe("seedFor", () => {
  // Expected values computed by the Python RunConfig.seed_for: the 32-bit
  // port must agree with it exactly, overflow included.
  const PYTHON: [number, number, number][] = [
    [0, 0, 0], [0, 1, 3936583297], [0, 2, 3820443219], [0, 199, 4207416219],
    [0, 9999, 2692100273], [1, 0, 1735533767], [1, 1, 525867145],
    [1, 2, 170485336], [1, 199, 3912140175], [1, 9999, 2480965930],
    [12345, 0, 948903385], [12345, 1, 842299649], [12345, 2, 2937501164],
    [12345, 199, 2527653], [12345, 9999, 2919217340], [4294967295, 0, 802968663],
    [4294967295, 1, 2314405428], [4294967295, 2, 1110915940],
    [4294967295, 199, 2037241269], [4294967295, 9999, 114070206],
  ];
  for (const [runSeed, index, expected] of PYTHON) {
    it(`run ${runSeed}, game ${index}`, () => {
      expect(seedFor(runSeed, index)).toBe(expected);
    });
  }
});

describe("rng", () => {
  it("is deterministic from its seed", () => {
    const a = newRng(42);
    const b = newRng(42);
    const xs = Array.from({ length: 20 }, () => nextUint32(a));
    expect(Array.from({ length: 20 }, () => nextUint32(b))).toEqual(xs);
    expect(new Set(xs).size).toBe(20);
    const c = newRng(43);
    expect(nextUint32(c)).not.toBe(xs[0]);
  });

  it("a clone is independent: drawing from it does not advance the original", () => {
    const real = newRng(7);
    nextUint32(real);
    const shadow = cloneRng(real);
    const expected = nextUint32(cloneRng(real));
    for (let i = 0; i < 50; i += 1) nextUint32(shadow);
    expect(nextUint32(real)).toBe(expected);
  });

  it("stays in range", () => {
    const rng = newRng(1);
    for (let i = 0; i < 2000; i += 1) {
      const f = random(rng);
      expect(f >= 0 && f < 1).toBe(true);
      const r = randrange(rng, 6);
      expect(r >= 0 && r < 6 && Number.isInteger(r)).toBe(true);
      const d = randint(rng, 1, 20);
      expect(d >= 1 && d <= 20).toBe(true);
    }
    expect(() => randrange(rng, 0)).toThrow();
    expect(() => choice(rng, [])).toThrow();
  });

  it("randrange covers every value", () => {
    const rng = newRng(3);
    const seen = new Set<number>();
    for (let i = 0; i < 500; i += 1) seen.add(randrange(rng, 10));
    expect(seen.size).toBe(10);
  });

  it("shuffle is a deterministic permutation", () => {
    const items = Array.from({ length: 100 }, (_, i) => i);
    const a = items.slice();
    const b = items.slice();
    shuffle(newRng(9), a);
    shuffle(newRng(9), b);
    expect(a).toEqual(b);
    expect(a).not.toEqual(items);
    expect(a.slice().sort((x, y) => x - y)).toEqual(items);
  });
});
