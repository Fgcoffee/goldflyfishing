// Ported from the destroy tests of tests/rules/test_actions.py (CR 701.7,
// 701.15, 702.12b).
import { describe, expect, it } from "vitest";
import { Zone } from "../../src/kernel/enums";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import { ir } from "../../src/rules/ir_read";
import { Board } from "./board";

describe("destroying (CR 701.7)", () => {
  it("puts a permanent into its owner's graveyard, once", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    expect(destroy(board.game, bears)).toBe(true);
    expect(board.game.battlefield).toHaveLength(0);
    expect(destroy(board.game, bears)).toBe(false); // the husk isn't a permanent (CR 110.1, 400.7)
  });

  it("indestructible stops it (CR 702.12b)", () => {
    const board = new Board();
    board.script("Grizzly Bears", { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Indestructible", text: "Indestructible" });
    const bears = board.play("Grizzly Bears");
    expect(destroy(board.game, bears)).toBe(false);
    expect(bears.zone).toBe(Zone.BATTLEFIELD);
  });

  it("a regeneration shield replaces it, unless it can't be regenerated (CR 701.15, 701.19c)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    bears.regenerationShields = 1;
    bears.damage = 2;
    expect(destroy(board.game, bears)).toBe(false);
    expect([bears.tapped, bears.damage, bears.regenerationShields]).toEqual([true, 0, 0]);
    bears.regenerationShields = 1;
    expect(destroy(board.game, bears, { regenerate: false })).toBe(true);
  });
});
