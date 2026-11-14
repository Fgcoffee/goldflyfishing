// Ported from tests/rules/test_cr608_untargeted_count.py: an untargeted
// "a creature you control" acts on that many, chosen deterministically; no
// count means every match (CR 608.2c-d, 107.1b). On the real matcher.
import { beforeEach, describe, expect, it } from "vitest";
import type { ObjectFilter, Value } from "../../src/ir";
import type { GameObject } from "../../src/kernel/gameobject";
import { counterCount } from "../../src/kernel/gameobject";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { installMatch } from "../../src/rules/match";
import { Board } from "./board";

const of = (n: number): Value => ({ $: "Value", constant: n });
const mine = (fields: Partial<ObjectFilter> = {}): ObjectFilter => ({
  $: "ObjectFilter",
  types_all: ir("CardType.CREATURE"),
  controller: ir("ControllerRelation.YOU"),
  ...fields,
});

let board: Board;
let creatures: GameObject[];
function setUp(): void {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
  creatures = [0, 1, 2].map(() => board.play("Grizzly Bears", 0));
}
beforeEach(setUp);

function countersOn(filter: ObjectFilter): void {
  execute(newResolution(board.game, 0, 0), [
    { $: "Effect", kind: ir("EffectKind.ADD_COUNTERS"), targets: filter, counter_type: "+1/+1", amount: of(1), text: "put a +1/+1 counter" },
  ]);
}
const counted = (): number[] => creatures.map((c) => counterCount(c, "+1/+1"));

describe("an untargeted count (CR 608.2c-d)", () => {
  it("a count of one affects one; of two, two", () => {
    countersOn(mine({ count: of(1) }));
    expect([...counted()].sort()).toEqual([0, 0, 1]);
    setUp();
    countersOn(mine({ count: of(2) }));
    expect([...counted()].sort()).toEqual([0, 1, 1]);
  });

  it("no count is every match; more than there are is all of them; up to zero is none (CR 107.1b)", () => {
    countersOn(mine());
    expect(counted()).toEqual([1, 1, 1]);
    setUp();
    countersOn(mine({ count: of(5) }));
    expect(counted()).toEqual([1, 1, 1]);
    setUp();
    countersOn(mine({ count: of(0), up_to: true }));
    expect(counted()).toEqual([0, 0, 0]);
  });

  it("the choice is deterministic, and never an opponent's creature", () => {
    countersOn(mine({ count: of(1) }));
    const first = counted();
    setUp();
    countersOn(mine({ count: of(1) }));
    expect(counted()).toEqual(first);
    setUp();
    const theirs = board.play("Grizzly Bears", 1);
    countersOn(mine({ count: of(3) }));
    expect(counterCount(theirs, "+1/+1")).toBe(0);
  });
});
