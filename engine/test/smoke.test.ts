import { describe, expect, it } from "vitest";
import { ENGINE_VERSION } from "../src/index";

describe("engine package", () => {
  it("builds and runs its tests", () => {
    expect(ENGINE_VERSION).toBe("0.0.1");
  });
});
