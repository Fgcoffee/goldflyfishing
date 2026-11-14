// Ported from tests/rules/test_resolve.py (scry, surveil, look at the top,
// put on the library, put onto the battlefield, explore).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { counterCount } from "../../src/kernel/gameobject";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const n = (v: number) => ({ $: "Value" as const, constant: v });

function stack(board: Board, ...names: string[]) {
  return names.map((name) => createObject(board.game, CARDS[name]!, 0, Zone.LIBRARY));
}

describe("the top of the library", () => {
  it("scry keeps lands and cheap cards on top, the rest to the bottom (CR 701.18)", () => {
    const board = new Board();
    const [angel, forest] = stack(board, "Serra Angel", "Forest", "Grizzly Bears");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("SCRY", { amount: n(2) })]);
    const lib = player(board.game, 0).library;
    expect(lib[0]).toBe(forest!.id);
    expect(board.game.objects.get(lib[lib.length - 1]!)!.previousId).toBe(angel!.id);
  });

  it("surveil puts the rejects in the graveyard (CR 701.44)", () => {
    const board = new Board();
    stack(board, "Serra Angel", "Forest");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("SURVEIL", { amount: n(2) })]);
    expect(player(board.game, 0).graveyard).toHaveLength(1);
    expect(player(board.game, 0).library).toHaveLength(1);
  });

  it("look at the top two, put one into your hand, the rest on the bottom", () => {
    const board = new Board();
    const [a, b, c] = stack(board, "Forest", "Grizzly Bears", "Island");
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [
      E("LOOK_AT_TOP", { amount: n(2) }),
      E("MOVE_ZONE", { zone: ir("Zone.HAND"), targets: filter({ from_pile: true, types_all: ir("CardType.CREATURE") }) }),
      E("PUT_ON_LIBRARY", { keywords: ["bottom"], targets: filter({ from_pile: true }) }),
    ]);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(player(board.game, 0).library).toEqual([c!.id, a!.id]);
    void b;
  });

  it("put onto the battlefield tapped, under its owner's control (CR 610.3c)", () => {
    const board = new Board();
    const card = createObject(board.game, CARDS["Grizzly Bears"]!, 1, Zone.GRAVEYARD);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[card.id]] }), [
      E("PUT_ONTO_BATTLEFIELD", { targets: filter({ zones: { $set: [Zone.GRAVEYARD] } }), is_targeted: true, keywords: ["tapped"], under_owners_control: true }),
    ]);
    const permanent = board.game.objects.get(card.supersededBy)!;
    expect([permanent.zone, permanent.tapped, permanent.controller]).toEqual([Zone.BATTLEFIELD, true, 1]);
  });

  it("explore: a land goes to hand, otherwise a +1/+1 counter (CR 701.40)", () => {
    const board = new Board();
    stack(board, "Forest", "Grizzly Bears");
    const bears = board.play("Grizzly Bears");
    execute(newResolution(board.game, bears.id, 0), [E("EXPLORE"), E("EXPLORE")]);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(counterCount(bears, "+1/+1")).toBe(1);
  });
});
