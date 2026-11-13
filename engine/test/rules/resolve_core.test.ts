// Ported from tests/rules/test_resolve.py (control flow, cards and zones) and
// test_cr608_remembered.py: what the first resolution executors do.
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { setAgent } from "./agent_util";
import { ALL_CREATURES, Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const players = (scope: string) => ({ $: "PlayerFilter" as const, scope: ir(`PlayerScope.${scope}` as never) });
const one = { $: "Value" as const, constant: 1 };

function stock(board: Board, owner: number, n: number) {
  for (let i = 0; i < n; i += 1) createObject(board.game, CARDS["Forest"]!, owner, Zone.LIBRARY);
}

describe("cards and zones", () => {
  it("draw and mill; milled cards become the pile", () => {
    const board = new Board();
    stock(board, 0, 5);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("DRAW", { amount: { $: "Value", constant: 2 } }), E("MILL", { amount: { $: "Value", constant: 2 } })]);
    expect(player(board.game, 0).hand).toHaveLength(2);
    expect(player(board.game, 0).graveyard).toHaveLength(2);
    expect(r.pile).toEqual(player(board.game, 0).graveyard);
  });

  it("destroy a target, and 'it' then names it in the graveyard (CR 608.2c, 400.7)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0, { targets: [[bears.id]] });
    execute(r, [E("DESTROY", { targets: ALL_CREATURES, is_targeted: true }), E("RETURN_TO_HAND", { targets: filter({ remembered: true }) })]);
    expect(player(board.game, 1).hand).toHaveLength(1);
  });

  it("a target that became illegal is skipped (CR 608.2b)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const source = board.play("Glorious Anthem");
    board.remove(bears);
    const r = newResolution(board.game, source.id, 0, { targets: [[bears.id]] });
    execute(r, [E("DESTROY", { targets: ALL_CREATURES, is_targeted: true })]);
    expect(player(board.game, 1).graveyard).toHaveLength(0);
  });

  it("each opponent sacrifices a creature of their own, the cheapest by default", () => {
    const board = new Board();
    board.play("Serra Angel", 1);
    const bears = board.play("Grizzly Bears", 1);
    const mine = board.play("Grizzly Bears", 0);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("SACRIFICE", { players: players("EACH_OPPONENT"), targets: filter({ ...ALL_CREATURES, count: one }) })]);
    expect(bears.supersededBy).not.toBe(0);
    expect(mine.zone).toBe(Zone.BATTLEFIELD);
    expect(r.remembered).toEqual([bears.id]);
  });

  it("discard asks the agent, else the last card in hand", () => {
    const board = new Board();
    stock(board, 0, 0);
    const a = createObject(board.game, CARDS["Forest"]!, 0, Zone.HAND);
    createObject(board.game, CARDS["Island"]!, 0, Zone.HAND);
    setAgent(board.game, 0, { chooseDiscard: () => a.id });
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("DISCARD", { amount: one })]);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(board.game.objects.get(player(board.game, 0).graveyard[0]!)!.previousId).toBe(a.id);
  });
});

describe("control flow", () => {
  it("'you may' is the controller's choice, and records whether it was taken", () => {
    const board = new Board();
    stock(board, 0, 3);
    const source = board.play("Glorious Anthem");
    setAgent(board.game, 0, { chooseOptional: () => false });
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("OPTIONAL", { children: [E("DRAW", { amount: one })] })]);
    expect(r.didIt).toBe(false);
    expect(player(board.game, 0).hand).toHaveLength(0);
  });

  it("repeat runs its children that many times", () => {
    const board = new Board();
    stock(board, 0, 5);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("REPEAT", { amount: { $: "Value", constant: 3 }, children: [E("DRAW", { amount: one })] })]);
    expect(player(board.game, 0).hand).toHaveLength(3);
  });

  it("'unless that player pays' happens when they can't pay", () => {
    const board = new Board();
    stock(board, 0, 2);
    const source = board.play("Glorious Anthem");
    const tax = { $: "Cost" as const, components: [{ $: "CostComponent" as const, kind: ir("CostKind.MANA"), mana: { $: "ManaCost" as const, symbols: [{ $: "ManaSymbol" as const, kind: ir("ManaSymbolKind.GENERIC"), generic: 1 }] } }] };
    execute(newResolution(board.game, source.id, 0), [E("UNLESS_PAYS", { players: players("EACH_OPPONENT"), pay_cost: tax, children: [E("DRAW", { amount: one })] })]);
    expect(player(board.game, 0).hand).toHaveLength(1);
  });
});
