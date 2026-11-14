/**
 * Numeric bounds on filters that read another object (test_filter_bounds.py).
 * match.test.ts has the X, counted-bound, of_affected, mixed-subject and
 * no-ability cases; these are the rest that test matching directly.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS, constant, effect, filter } from "../../../test/rules/board";
import type { ObjectFilter, Value } from "../../ir";
import { CardType, Zone } from "../../kernel/enums";
import { createObject } from "../../kernel/game";
import { ir } from "../ir_read";
import { execute, newResolution } from "../cr600_spells_and_abilities/resolve";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { Comparison, ControllerRelation, ValueKind } from "./enums";
import { installMatch, matches } from "./index";

const bound = (comparison: number, v: Value) => ({ $: "NumericConstraint", comparison, value: v }) as never;
const value = (fields: Partial<Value>): Value => ({ $: "Value", ...fields }) as Value;

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
  board.game.activePlayer = 0;
});

describe("bounds that read the ability's side", () => {
  it("'mana value <= the number of lands you control' counts the controller's lands, swept", () => {
    for (let i = 0; i < 3; i++) board.play("Forest", 0);
    const disk = board.play("Glorious Anthem", 0); // the ability's source, as Nevinyrral's Disk in Python
    const bears = board.play("Grizzly Bears", 1); // mana value 2
    const angel = board.play("Serra Angel", 1); // mana value 5
    const landsYouControl = value({ kind: ValueKind.COUNT, filter: filter({ types_any: CardType.LAND, controller: ControllerRelation.YOU } as Partial<ObjectFilter>) });
    execute(newResolution(board.game, disk.id, 0), [
      effect({ kind: ir("EffectKind.DESTROY"), targets: filter({ types_any: CardType.CREATURE, mana_value: bound(Comparison.LE, landsYouControl) } as Partial<ObjectFilter>) }),
    ]);
    expect(board.game.battlefield).not.toContain(bears.id);
    expect(board.game.battlefield).toContain(angel.id);
  });

  it("a characteristic without of_affected is the source's (transmute's 'same mana value')", () => {
    const source = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.GRAVEYARD); // mana value 2
    const same = createObject(board.game, CARDS["Tarmogoyf"]!, 0, Zone.LIBRARY); // mana value 2
    const bigger = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.LIBRARY); // mana value 5
    board.refresh();
    const spec = filter({ zones: { $set: [Zone.LIBRARY] }, mana_value: bound(Comparison.EQ, value({ kind: ValueKind.MANA_VALUE })) } as Partial<ObjectFilter>);
    expect(matches(board.game, same, spec, { source: source.id, controller: 0 })).toBe(true);
    expect(matches(board.game, bigger, spec, { source: source.id, controller: 0 })).toBe(false);
  });

  it("a constant bound still works", () => {
    const bears = board.play("Grizzly Bears", 0);
    expect(matches(board.game, bears, filter({ mana_value: bound(Comparison.LE, constant(2)) } as Partial<ObjectFilter>), { controller: 0 })).toBe(true);
  });
});
