// Ported from the scenarios of tests/rules/test_cr601_mana_plan.py and
// test_mana_plan_stale_source.py. The Python tests drive real cards on a
// board; until casting is ported, the same situations are built here as
// mana options, which is what the planner sees.
import { describe, expect, it } from "vitest";
import { Color } from "../../kernel/enums";
import type { Ability } from "../../ir";
import {
  Drawback,
  executePlan,
  manaKind,
  newManaPool,
  parseCost,
  planPayment,
  PlanNoLongerHolds,
  sortSourceOptions,
  type ManaOption,
  type ManaPlanHost,
} from "./index";

const C = manaKind(Color.NONE);
const W = manaKind(Color.WHITE);
const R = manaKind(Color.RED);
const G = manaKind(Color.GREEN);

function option(source: number, produces: typeof C[], extra: Partial<ManaOption> = {}): ManaOption {
  return { source, abilityIndex: 0, modes: [], color: Color.NONE, produces, manaCost: null, drawback: Drawback.FREE, ...extra };
}

/** Karplusan Forest: {T}: Add {C}. {T}: Add {R} or {G}; it deals 1 damage to you. */
function karplusan(source: number): ManaOption[] {
  return sortSourceOptions([
    option(source, [R], { abilityIndex: 1, color: Color.RED, drawback: Drawback.PAINFUL }),
    option(source, [C]),
    option(source, [G], { abilityIndex: 1, color: Color.GREEN, drawback: Drawback.PAINFUL }),
  ]);
}

const forest = (source: number) => [option(source, [G])];
const mountain = (source: number) => [option(source, [R])];

function plan(cost: string, sources: ManaOption[][], life = 40) {
  return planPayment({ pool: newManaPool(), life, cost: parseCost(cost), sources });
}

describe("mana planning (CR 601.2g)", () => {
  it("a pain land pays for the colour it can make", () => {
    const chosen = plan("{R}", [karplusan(1)]);
    expect(chosen).not.toBeNull();
    expect(chosen!.map((o) => o.produces[0])).toEqual([R]);
    expect(chosen![0]!.drawback).toBe(Drawback.PAINFUL);
  });

  it("a basic land is tapped before a pain land", () => {
    const chosen = plan("{R}", [karplusan(1), mountain(2)]);
    expect(chosen!.map((o) => o.source)).toEqual([2]);
  });

  it("the pain land's {C} is preferred when it is enough", () => {
    const chosen = plan("{1}", [karplusan(1)]);
    expect(chosen!.map((o) => o.produces[0])).toEqual([C]);
  });

  it("any colour is the colour the cost needs", () => {
    const anyColour = [W, manaKind(Color.BLUE), manaKind(Color.BLACK), R, G].map((k) =>
      option(1, [k], { color: k.color }),
    );
    expect(plan("{G}", [anyColour])!.map((o) => o.color)).toEqual([Color.GREEN]);
  });

  it("only what the cost needs is tapped", () => {
    const chosen = plan("{R}", [forest(1), forest(2), mountain(3)]);
    expect(chosen!.map((o) => o.source)).toEqual([3]);
  });

  it("a Signet is paid for by another source", () => {
    // Signet: {1}, {T}: Add {W}{R}.
    const signet = [option(2, [W, R], { manaCost: parseCost("{1}") })];
    const chosen = plan("{W}{R}", [forest(1), signet]);
    expect(chosen!.map((o) => o.source).sort()).toEqual([1, 2]);
    expect(plan("{W}{R}", [signet])).toBeNull();
  });

  it("a colour nothing can make is not offered", () => {
    expect(plan("{W}", [forest(1), mountain(2)])).toBeNull();
  });

  it("an empty plan when the pool already pays", () => {
    const pool = newManaPool();
    pool.buckets.set("x", { kind: R, count: 1 });
    expect(planPayment({ pool, life: 40, cost: parseCost("{R}"), sources: [] })).toEqual([]);
  });

  it("a Phyrexian symbol is paid with life when no source makes the colour, keeping one life", () => {
    expect(plan("{G/P}", [mountain(1)], 40)).toEqual([]);
    expect(plan("{G/P}", [mountain(1)], 2)).toBeNull();
  });

  it("is deterministic", () => {
    const a = plan("{2}{G}", [forest(3), mountain(1), forest(2), karplusan(4)]);
    const b = plan("{2}{G}", [forest(3), mountain(1), forest(2), karplusan(4)]);
    expect(a).toEqual(b);
  });
});

describe("executing a plan: the F61 guard", () => {
  it("re-checks each source before activating it", () => {
    const activated: number[] = [];
    const gone = new Set([2]);
    const host: ManaPlanHost<null> = {
      manaOptions: () => [],
      stillActivatable: (_game, o) => (gone.has(o.source) ? null : ({ $: "Ability" } as unknown as Ability)),
      activate: (_game, _player, o) => {
        activated.push(o.source);
      },
    };
    expect(() => executePlan(host, null, 0, [option(1, [G]), option(2, [G])])).toThrow(PlanNoLongerHolds);
    expect(activated).toEqual([1]);
  });
});
