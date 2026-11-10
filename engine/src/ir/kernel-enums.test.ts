import { describe, expect, it } from "vitest";
import * as Kernel from "../kernel/enums";
import * as IR from "./types";

/**
 * The card IR's enum tables (generated with the schema) and the kernel's
 * enums (generated from the same Python enums by the rules session) must
 * agree member for member: the IR is written in one, the engine computes in
 * the other. If this fails, one side was regenerated and the other was not.
 */
describe("IR enums match the kernel's", () => {
  const shared: string[] = [];
  for (const [key, table] of Object.entries(IR)) {
    const match = /^(.*)(Values|Bits)$/.exec(key);
    if (match === null || typeof table !== "object") continue;
    const name = match[1] as string;
    if (name in Kernel && typeof (Kernel as Record<string, unknown>)[name] === "object") {
      shared.push(name);
    }
  }

  it("shares the core enums", () => {
    for (const name of ["Zone", "CardType", "Color", "Supertype", "Timing", "Duration", "Phase", "Step"]) {
      expect(shared).toContain(name);
    }
  });

  it.each(shared)("%s has the same members and values", (name) => {
    const ir = ((IR as Record<string, unknown>)[`${name}Values`] ??
      (IR as Record<string, unknown>)[`${name}Bits`]) as Record<string, number>;
    const kernel = (Kernel as Record<string, unknown>)[name] as Record<string, number>;
    expect(ir).toEqual({ ...kernel });
  });
});
