/**
 * A characteristic of the object the text refers to (CR 608.2h, 603.2).
 * Ported from the engine-side cases of tests/rules/test_cr608_affected_values.py
 * (the parser and Sandbox cases stay in Python).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, effect, filter } from "../../../test/rules/board";
import type { Ability, Value } from "../../ir";
import { ir } from "../ir_read";
import { execute, newResolution } from "../cr600_spells_and_abilities/resolve";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { PlayerScope, ValueKind } from "./enums";
import { evaluate, installMatch } from "./index";

const REMEMBERED = filter({ remembered: true });
const powerOfIt = { $: "Value", kind: ValueKind.POWER, filter: REMEMBERED } as Value;

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
  board.game.activePlayer = 0;
});

describe("the object the text refers to", () => {
  it("a named object's value reads that object, and outside a resolution is zero", () => {
    const angel = board.play("Serra Angel", 1);
    const bears = board.play("Grizzly Bears", 0);
    expect(evaluate(board.game, powerOfIt, { source: bears.id, remembered: [angel.id] })).toBe(4);
    // Nothing remembered is not the source: zero, not the Bears' 2.
    expect(evaluate(board.game, powerOfIt, { source: bears.id })).toBe(0);
  });

  it("the named dealer deals the damage, so its lifelink gains its controller life (CR 120.1)", () => {
    const lifelink: Ability = { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Lifelink", text: "Lifelink" };
    board.script("Grizzly Bears", lifelink);
    const bears = board.play("Grizzly Bears", 0);
    const resolution = newResolution(board.game, 0, 0);
    resolution.remembered = [bears.id];
    execute(resolution, [
      effect({
        kind: ir("EffectKind.DAMAGE"),
        players: { $: "PlayerFilter", scope: PlayerScope.EACH_OPPONENT },
        amount: powerOfIt,
        damage_source: REMEMBERED,
      }),
    ]);
    expect(board.game.players[1]!.life).toBe(38);
    expect(board.game.players[0]!.life).toBe(42);
  });
});
