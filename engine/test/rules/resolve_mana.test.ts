// Ported from the mana part of tests/rules/test_resolve.py and
// test_cr106_mana.py: _do_add_mana and resolve_mana_ability (CR 106, 605.3b).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Color } from "../../src/kernel/enums";
import { EventKind, type GameEvent } from "../../src/kernel/events";
import { player } from "../../src/kernel/game";
import { newResolution, resolveManaAbility } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { amountOfColor, poolTotal } from "../../src/rules/mana/pool";
import { Board, effect } from "./board";

const addMana = (fields: Partial<Effect>): Effect => effect({ kind: ir("EffectKind.ADD_MANA"), ...fields });

function watch(board: Board): GameEvent[] {
  const seen: GameEvent[] = [];
  board.game.observer = (_g, e) => void seen.push(e);
  return seen;
}

describe("adding mana (CR 106.1)", () => {
  it("adds exactly the printed symbols: {G}{U} is one green and one blue", () => {
    const board = new Board();
    const land = board.play("Bayou");
    resolveManaAbility(newResolution(board.game, land.id, 0), [addMana({ mana_produced: ["{G}", "{U}"] })], { tapped: true });
    const pool = player(board.game, 0).manaPool;
    expect(poolTotal(pool)).toBe(2);
    expect(amountOfColor(pool, Color.GREEN)).toBe(1);
    expect(amountOfColor(pool, Color.BLUE)).toBe(1);
  });

  it("'any color' takes the payer's colour if on the menu, else the first", () => {
    const board = new Board();
    const land = board.play("Forest");
    const any = addMana({ colors: (Color.WHITE | Color.BLUE | Color.BLACK | Color.RED | Color.GREEN) as never });
    resolveManaAbility(newResolution(board.game, land.id, 0, { manaColor: Color.RED }), [any], { tapped: false });
    resolveManaAbility(newResolution(board.game, land.id, 0), [any], { tapped: false });
    const pool = player(board.game, 0).manaPool;
    expect(amountOfColor(pool, Color.RED)).toBe(1);
    expect(amountOfColor(pool, Color.WHITE)).toBe(1);
  });

  it("tapped for mana once, with the colours produced, only when {T} was paid (CR 106.12a)", () => {
    const board = new Board();
    const land = board.play("Bayou");
    const seen = watch(board);
    const both = [effect({ kind: ir("EffectKind.SEQUENCE"), children: [addMana({ mana_produced: ["{B}"] }), addMana({ mana_produced: ["{G}"] })] })];
    resolveManaAbility(newResolution(board.game, land.id, 0), both, { tapped: true });
    const tapped = seen.filter((e) => e.kind === EventKind.TAPPED_FOR_MANA);
    expect(tapped).toHaveLength(1);
    expect(tapped[0]!.data).toEqual([Color.BLACK, Color.GREEN]);
    expect(seen.filter((e) => e.kind === EventKind.MANA_ADDED)).toHaveLength(2);
    resolveManaAbility(newResolution(board.game, land.id, 0), both, { tapped: false });
    expect(seen.filter((e) => e.kind === EventKind.TAPPED_FOR_MANA)).toHaveLength(1);
  });

  it("an effect with no executor is logged and skipped, as in Python", () => {
    const board = new Board();
    const land = board.play("Forest");
    resolveManaAbility(newResolution(board.game, land.id, 0), [effect({ kind: ir("EffectKind.UNPARSED"), text: "something unread" })], { tapped: true });
    expect(poolTotal(player(board.game, 0).manaPool)).toBe(0);
  });
});
