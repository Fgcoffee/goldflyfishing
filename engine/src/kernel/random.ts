/**
 * The game's one source of randomness. Replaces Python's `random.Random`
 * (mtgfish/rules/kernel/game.py `game.rng`, cr103_setup) and the run's seed
 * derivation (mtgfish/sim/runner.py `RunConfig.seed_for`).
 *
 * sfc32: a small, fast, well-tested 32-bit generator whose whole state is four
 * integers, so it is plain data - a lookahead's shadow game copies it and
 * planning never advances the real game's stream. Never use Math.random.
 * Seed-for-seed parity with the Python engine is not a goal.
 */

/** sfc32 state: four 32-bit words. */
export interface Rng {
  a: number;
  b: number;
  c: number;
  d: number;
}

/** A generator seeded from one 32-bit integer (splitmix32 expands the seed). */
export function newRng(seed: number): Rng {
  let s = seed >>> 0;
  const next = (): number => {
    s = (s + 0x9e3779b9) >>> 0;
    let z = s;
    z = Math.imul(z ^ (z >>> 16), 0x85ebca6b);
    z = Math.imul(z ^ (z >>> 13), 0xc2b2ae35);
    return (z ^ (z >>> 16)) >>> 0;
  };
  const rng: Rng = { a: next(), b: next(), c: next(), d: next() };
  // Discard the first outputs, as sfc32's authors recommend.
  for (let i = 0; i < 12; i += 1) nextUint32(rng);
  return rng;
}

export function cloneRng(rng: Rng): Rng {
  return { a: rng.a, b: rng.b, c: rng.c, d: rng.d };
}

/** The next 32-bit unsigned integer. */
export function nextUint32(rng: Rng): number {
  const t = (((rng.a + rng.b) >>> 0) + rng.d) >>> 0;
  rng.d = (rng.d + 1) >>> 0;
  rng.a = (rng.b ^ (rng.b >>> 9)) >>> 0;
  rng.b = (rng.c + (rng.c << 3)) >>> 0;
  rng.c = ((rng.c << 21) | (rng.c >>> 11)) >>> 0;
  rng.c = (rng.c + t) >>> 0;
  return t;
}

/** A float in [0, 1) (Python `random()`). */
export function random(rng: Rng): number {
  return nextUint32(rng) / 4294967296;
}

/**
 * An integer in [0, n) (Python `randrange(n)`), without modulo bias: values
 * from the incomplete top bucket are redrawn.
 */
export function randrange(rng: Rng, n: number): number {
  if (!Number.isInteger(n) || n <= 0) throw new RangeError(`randrange(${n})`);
  if (n > 4294967296) throw new RangeError(`randrange(${n}) is beyond 32 bits`);
  const limit = 4294967296 - (4294967296 % n);
  for (;;) {
    const x = nextUint32(rng);
    if (x < limit) return x % n;
  }
}

/** An integer in [low, high], both inclusive (Python `randint`). */
export function randint(rng: Rng, low: number, high: number): number {
  return low + randrange(rng, high - low + 1);
}

/** One element, uniformly (Python `choice`). Throws on an empty array. */
export function choice<T>(rng: Rng, items: readonly T[]): T {
  if (items.length === 0) throw new RangeError("choice from an empty sequence");
  return items[randrange(rng, items.length)] as T;
}

/** Shuffle in place, Fisher-Yates (Python `shuffle`). */
export function shuffle<T>(rng: Rng, items: T[]): void {
  for (let i = items.length - 1; i > 0; i -= 1) {
    const j = randrange(rng, i + 1);
    const tmp = items[i] as T;
    items[i] = items[j] as T;
    items[j] = tmp;
  }
}

/**
 * The seed of game `index` in a run (Python `RunConfig.seed_for`): derived
 * arithmetically, so game N is reproducible without playing the N-1 before
 * it. The Python version multiplies past 2^53 before masking; Math.imul keeps
 * every step in 32 bits, which is the same arithmetic modulo 2^32.
 */
export function seedFor(runSeed: number, index: number): number {
  let mixed = (Math.imul(runSeed, 0x9e3779b1) + Math.imul(index, 0x85ebca77)) >>> 0;
  mixed = (mixed ^ (mixed >>> 15)) >>> 0;
  mixed = Math.imul(mixed, 0x2545f491) >>> 0;
  return (mixed ^ (mixed >>> 13)) >>> 0;
}
