/**
 * Values only a resolution can answer (CR 608.2c, 608.2h). Ported from
 * tests/rules/test_cr608_values_this_way.py, through the rules session's
 * resolve.ts.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, constant, effect, filter } from "../../../test/rules/board";
import type { Effect, Value } from "../../ir";
import { CardType } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { ir } from "../ir_read";
import { execute, newResolution } from "../cr600_spells_and_abilities/resolve";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { ControllerRelation, PlayerScope, ValueKind } from "./enums";
import { evaluate, installMatch } from "./index";

const players = (scope: number) => ({ $: "PlayerFilter", scope }) as const;
const value = (fields: Partial<Value>): Value => ({ $: "Value", ...fields }) as Value;
const LIFE_LOST_THIS_WAY = value({ kind: ValueKind.AMOUNT_THIS_WAY, event_kinds: [EventKind.LIFE_LOST] });
const gain = (amount: Value): Effect => effect({ kind: ir("EffectKind.GAIN_LIFE"), players: players(PlayerScope.YOU), amount });

let board: Board;
const run = (...effects: Effect[]) => execute(newResolution(board.game, 0, 0), effects);
const life = (id: number) => board.game.players[id]!.life;

beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
  board.game.activePlayer = 0;
});

describe("amounts this way (CR 608.2c)", () => {
  it("life lost this way is the amount lost", () => {
    run(effect({ kind: ir("EffectKind.LOSE_LIFE"), players: players(PlayerScope.EACH_OPPONENT), amount: constant(2) }), gain(LIFE_LOST_THIS_WAY));
    expect(life(1)).toBe(38);
    expect(life(0)).toBe(42);
  });

  it("damage dealt this way counts damage", () => {
    run(
      effect({ kind: ir("EffectKind.DAMAGE"), players: players(PlayerScope.EACH_OPPONENT), amount: constant(3) }),
      gain(value({ kind: ValueKind.AMOUNT_THIS_WAY, event_kinds: [EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT] })),
    );
    expect(life(0)).toBe(43);
  });

  it("nothing done this way is zero", () => {
    run(gain(LIFE_LOST_THIS_WAY));
    expect(life(0)).toBe(40);
  });

  it("'destroyed this way' counts only what was destroyed (Fumigate)", () => {
    for (let i = 0; i < 3; i++) board.play("Grizzly Bears", 1);
    for (let i = 0; i < 4; i++) board.play("Forest", 0);
    run(
      effect({ kind: ir("EffectKind.DESTROY"), targets: filter({ types_all: CardType.CREATURE }) }),
      gain(value({ kind: ValueKind.COUNT, filter: filter({ types_all: CardType.CREATURE, remembered: true }) })),
    );
    expect(life(0)).toBe(43);
  });

  it("a remembered count outside a resolution is zero", () => {
    board.play("Grizzly Bears", 0);
    expect(evaluate(board.game, value({ kind: ValueKind.COUNT, filter: filter({ remembered: true }) }), { controller: 0 })).toBe(0);
  });

  it("life gained this turn is an amount, not a life total", () => {
    const gained = value({ kind: ValueKind.EVENT_AMOUNT_THIS_TURN, players: players(PlayerScope.YOU), event_kinds: [EventKind.LIFE_GAINED] });
    expect(evaluate(board.game, gained, { controller: 0 })).toBe(0);
    run(gain(constant(2)));
    run(gain(constant(3)));
    expect(evaluate(board.game, gained, { controller: 0 })).toBe(5);
  });
});

describe("numbers fixed as it resolves (CR 608.2h)", () => {
  it("a resolving pump that counts Forests stays the size it was", () => {
    const bear = board.play("Grizzly Bears", 0);
    board.play("Forest", 0);
    const forests = value({ kind: ValueKind.COUNT, filter: filter({ subtypes_all: ["Forest"], controller: ControllerRelation.YOU }) });
    run(
      effect({
        kind: ir("EffectKind.MODIFY_PT"),
        targets: filter({ types_all: CardType.CREATURE, controller: ControllerRelation.YOU }),
        amount: forests,
        amount2: forests,
        duration: ir("Duration.END_OF_TURN"),
      }),
    );
    board.refresh();
    expect(board.pt(bear)).toEqual([3, 3]);
    board.play("Forest", 0);
    board.refresh();
    expect(board.pt(bear)).toEqual([3, 3]);
  });
});
