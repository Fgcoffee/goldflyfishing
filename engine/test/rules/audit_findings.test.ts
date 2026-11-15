// The rules-owned findings in audit/AUDIT_LOG.md, on the TS engine: each
// describe names its finding and shows whether it still happens. Fixed
// ones are plain tests; one still open is `it.fails`, so it turns red the
// day it is fixed and gets moved over.
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const n = (v: number) => ({ $: "Value" as const, constant: v });
const draw = (k: number) => E("DRAW", { amount: n(k) });

describe("F62: MOVE_ZONE to the library", () => {
  it("goes to the library, not the graveyard", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [
      E("MOVE_ZONE", { zone: Zone.LIBRARY as never, targets: filter({ specific: [bears.id] }) }),
    ]);
    const lib = player(board.game, 0).library.map((id) => board.game.objects.get(id)!);
    expect(lib.map((o) => (o.card as { name: string }).name)).toEqual(["Grizzly Bears"]);
    expect(player(board.game, 0).graveyard).toHaveLength(0);
  });

  it("with no destination it still goes to the graveyard", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("MOVE_ZONE", { targets: filter({ specific: [bears.id] }) })]);
    expect(player(board.game, 0).graveyard).toHaveLength(1);
  });
});

describe("F64: VOTE", () => {
  it("runs from the next player in turn order without crashing, and the winner's child happens (CR 701.34)", () => {
    const board = new Board();
    for (const name of ["Forest", "Forest"]) createObject(board.game, CARDS[name]!, 0, Zone.LIBRARY);
    const source = board.play("Glorious Anthem");
    const order: number[] = [];
    for (const pid of [0, 1]) board.game.agents.set(pid, { chooseVote: (_g: unknown, p: number) => (order.push(p), 1) } as never);
    execute(newResolution(board.game, source.id, 0), [E("VOTE", { children: [draw(1), draw(2)] })]);
    expect(order).toEqual([1, 0]);
    expect(player(board.game, 0).hand).toHaveLength(2);
  });
});
