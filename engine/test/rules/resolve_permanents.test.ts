// Ported from tests/rules/test_resolve.py (counters, attach, counter a spell)
// and test_cr611_continuous.py (effects from resolving spells, CR 611.2c, 608.2h).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { counterCount } from "../../src/kernel/gameobject";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const n = (v: number) => ({ $: "Value" as const, constant: v });

describe("counters", () => {
  it("divided counters are shared out, the remainder to the first (CR 601.2d)", () => {
    const board = new Board();
    const a = board.play("Grizzly Bears");
    const b = board.play("Serra Angel");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[a.id, b.id]] }), [
      E("ADD_COUNTERS", { targets: filter({ ...ALL_CREATURES, count: n(2) }), is_targeted: true, divided: true, counter_type: "+1/+1", amount: n(3) }),
    ]);
    expect([counterCount(a, "+1/+1"), counterCount(b, "+1/+1")]).toEqual([2, 1]);
  });

  it("proliferate adds one of each kind to your own permanents", () => {
    const board = new Board();
    const mine = board.play("Grizzly Bears");
    const theirs = board.play("Grizzly Bears", 1);
    mine.counters.set("+1/+1", 1);
    theirs.counters.set("+1/+1", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("PROLIFERATE")]);
    expect([counterCount(mine, "+1/+1"), counterCount(theirs, "+1/+1")]).toEqual([2, 1]);
  });
});

describe("the stack and permanents", () => {
  it("counter target spell puts it in its owner's graveyard (CR 701.5)", () => {
    const board = new Board();
    const spell = createObject(board.game, CARDS["Grizzly Bears"]!, 1, Zone.STACK);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[spell.id]] }), [
      E("COUNTER_SPELL", { targets: filter({ zones: { $set: [Zone.STACK] } }), is_targeted: true }),
    ]);
    expect(board.game.stack).toHaveLength(0);
    expect(player(board.game, 1).graveyard).toHaveLength(1);
  });

  it("attach the source to a target", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const aura = board.play("Levitation");
    execute(newResolution(board.game, aura.id, 0, { targets: [[bears.id]] }), [E("ATTACH", { targets: ALL_CREATURES, is_targeted: true })]);
    expect(aura.attachedTo).toBe(bears.id);
  });
});

describe("continuous effects from a resolving spell (CR 611.2)", () => {
  it("'creatures you control get +1/+1' settles its set as it begins (CR 611.2c)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [
      E("MODIFY_PT", { targets: ALL_CREATURES, amount: n(1), amount2: n(1), duration: ir("Duration.END_OF_TURN") }),
    ]);
    const later = board.play("Serra Angel");
    expect(characteristicsOf(board.game, bears).power).toBe(3);
    expect(characteristicsOf(board.game, later).power).toBe(4);
  });

  it("gain control of target creature (layer 2)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[bears.id]] }), [
      E("GAIN_CONTROL", { targets: ALL_CREATURES, is_targeted: true, duration: ir("Duration.PERMANENT") }),
    ]);
    board.refresh();
    characteristicsOf(board.game, bears);
    expect(bears.controller).toBe(0);
  });
});
