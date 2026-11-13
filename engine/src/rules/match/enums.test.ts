import { describe, expect, it } from "vitest";
import * as IR from "../../ir";
import * as Local from "./enums";

describe("match/ enums agree with the IR tables", () => {
  it.each(["ControllerRelation", "PlayerScope", "ValueKind", "ConditionKind", "Comparison"])("%s", (name) => {
    const ir = (IR as Record<string, unknown>)[`${name}Values`];
    expect({ ...(Local as Record<string, object>)[name] }).toEqual(ir);
  });
});
