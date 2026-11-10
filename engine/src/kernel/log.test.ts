// Ported from mtgfish/rules/kernel/log.py and its determinism checks.
import { describe, expect, it } from "vitest";
import { Phase, Step } from "./enums";
import { NO_PLAYER } from "./ids";
import { cloneGameLog, entryText, logDigest, newGameLog, popDepth, pushDepth, record } from "./log";

const clock = { turn: 3, phase: Phase.PRECOMBAT_MAIN, step: Step.MAIN, activePlayer: 0 };

describe("game log", () => {
  it("keeps the digest even when logging is off, and nothing else", () => {
    const off = newGameLog(false);
    const on = newGameLog(true);
    for (const log of [off, on]) {
      record(log, clock, "Grizzly Bears enters", { kind: "event", player: 0 });
      record(log, clock, "Shock deals 2 damage");
    }
    expect(off.entries).toHaveLength(0);
    expect(on.entries).toHaveLength(2);
    expect(logDigest(off)).toBe(logDigest(on));
    expect(logDigest(off)).toMatch(/^[0-9a-f]{16}$/);
  });

  it("the digest changes with any change to what was recorded", () => {
    const a = newGameLog();
    const b = newGameLog();
    record(a, clock, "x");
    record(b, clock, "y");
    expect(logDigest(a)).not.toBe(logDigest(b));
    const c = newGameLog();
    record(c, { ...clock, turn: 4 }, "x");
    expect(logDigest(c)).not.toBe(logDigest(a));
    expect(logDigest(newGameLog())).not.toBe(logDigest(a));
  });

  it("a cloned log diverges without touching the original", () => {
    const real = newGameLog(true);
    record(real, clock, "before");
    const before = logDigest(real);
    const shadow = cloneGameLog(real);
    record(shadow, clock, "planning only");
    expect(logDigest(real)).toBe(before);
    expect(real.entries).toHaveLength(1);
  });

  it("indents nested entries", () => {
    const log = newGameLog(true);
    pushDepth(log);
    record(log, clock, "inner");
    popDepth(log);
    popDepth(log);
    expect(log.depth).toBe(0);
    const entry = log.entries[0];
    expect(entry?.player).toBe(NO_PLAYER);
    expect(entry && entryText(entry)).toBe(`T3 ${"main".padEnd(24)}   inner`);
  });
});
