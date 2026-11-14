// Ported from tests/rules/test_resolve.py (players, paying during a
// resolution) and test_cr701_search.py (CR 701.23).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const lands = filter({ types_all: ir("CardType.LAND"), zones: { $set: [Zone.LIBRARY] } });

function library(board: Board) {
  for (let i = 0; i < 4; i += 1) createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.LIBRARY);
  return createObject(board.game, CARDS["Forest"]!, 0, Zone.LIBRARY);
}

describe("searching a library (CR 701.23)", () => {
  it("finds a land, puts it onto the battlefield tapped, and shuffles", () => {
    const board = new Board();
    library(board);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("SEARCH_LIBRARY", { targets: lands, zone: ir("Zone.BATTLEFIELD"), keywords: ["tapped"] })]);
    const land = board.game.objects.get(r.remembered[0]!)!;
    const onField = board.game.objects.get(land.supersededBy)!;
    expect(onField.zone).toBe(Zone.BATTLEFIELD);
    expect(onField.tapped).toBe(true);
    expect(player(board.game, 0).library).toHaveLength(4);
  });

  it("'then shuffle and put that card on top' puts it on top after the shuffle", () => {
    const board = new Board();
    const forest = library(board);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("SEARCH_LIBRARY", { targets: lands, zone: ir("Zone.LIBRARY") })]);
    expect(player(board.game, 0).library[0]).toBe(forest.id);
    expect(player(board.game, 0).library).toHaveLength(5);
  });
});

describe("players", () => {
  it("'you may pay'; 'if you don't' runs only when it wasn't paid", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const source = board.play("Glorious Anthem");
    const two = { $: "Cost" as const, components: [{ $: "CostComponent" as const, kind: ir("CostKind.MANA"), mana: { $: "ManaCost" as const, symbols: [{ $: "ManaSymbol" as const, kind: ir("ManaSymbolKind.GENERIC"), generic: 2 }] } }] };
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("PAY_COST", { pay_cost: two }), E("IF_YOU_DONT", { children: [E("LOSE_LIFE", { amount: { $: "Value", constant: 3 } })] })]);
    expect(r.didIt).toBe(false);
    expect(player(board.game, 0).life).toBe(37);
  });

  it("the monarch is one player; an extra land drop raises the limit", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    player(board.game, 1).isMonarch = true;
    execute(newResolution(board.game, source.id, 0), [E("BECOME_MONARCH"), E("EXTRA_LAND_DROP")]);
    expect(board.game.players.map((p) => p.isMonarch)).toEqual([true, false]);
    expect(player(board.game, 0).maxLands).toBe(2);
  });
});

