// Ported from tests/rules/test_resolve.py (monstrosity, adapt, exchanges,
// end the turn, piles, regenerate).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { counterCount } from "../../src/kernel/gameobject";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const n = (v: number) => ({ $: "Value" as const, constant: v });

describe("more executors", () => {
  it("monstrosity happens once; adapt only with no +1/+1 counters", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const r = () => newResolution(board.game, bears.id, 0);
    execute(r(), [E("MONSTROSITY", { amount: n(2) }), E("MONSTROSITY", { amount: n(2) }), E("ADAPT", { amount: n(3) })]);
    expect(counterCount(bears, "+1/+1")).toBe(2);
    expect(counterCount(bears, "monstrous")).toBe(1);
  });

  it("exchange control swaps both, base and all, or neither (CR 701.10)", () => {
    const board = new Board();
    const mine = board.play("Grizzly Bears", 0);
    const theirs = board.play("Serra Angel", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[mine.id, theirs.id]] }), [
      E("EXCHANGE_CONTROL", { targets: filter({ ...ALL_CREATURES, count: n(2) }), is_targeted: true }),
    ]);
    expect([mine.controller, theirs.controller, mine.baseController, theirs.baseController]).toEqual([1, 0, 1, 0]);
  });

  it("end the turn exiles the stack (CR 724)", () => {
    const board = new Board();
    createObject(board.game, CARDS["Grizzly Bears"]!, 1, Zone.STACK);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("END_TURN")]);
    expect(board.game.stack).toHaveLength(0);
    expect(board.game.endTurnRequested).toBe(true);
  });

  it("regenerate gives a shield destroy spends", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    execute(newResolution(board.game, bears.id, 0), [E("REGENERATE")]);
    expect(destroy(board.game, bears)).toBe(false);
    expect(bears.regenerationShields).toBe(0);
  });

  it("piles: the chooser takes the pile worth more; children act on it, otherwise on the rest", () => {
    const board = new Board();
    board.play("Serra Angel", 1);
    board.play("Grizzly Bears", 1);
    board.play("Grizzly Bears", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [
      E("SEPARATE_INTO_PILES", {
        targets: ALL_CREATURES,
        children: [E("RETURN_TO_HAND", { targets: filter({ remembered: true }) })],
        otherwise: [E("DESTROY", { targets: filter({ remembered: true }) })],
      }),
    ]);
    // Alternating from the highest mana value: [Angel, Bears] vs [Bears]; Angel's pile is worth more.
    expect(player(board.game, 1).hand).toHaveLength(2);
    expect(player(board.game, 1).graveyard).toHaveLength(1);
  });
});
