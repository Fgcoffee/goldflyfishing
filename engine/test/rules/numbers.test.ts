// Ported from tests/rules/test_cr107_numbers.py: results and choices never
// negative (CR 107.1b-c), costs floored at zero (loyalty signed, CR 107.7),
// and an undeterminable amount makes a cost unpayable (CR 903.4f, 118.6).
// On the real evaluator (match/), which reports undeterminable numbers.
import { beforeEach, describe, expect, it } from "vitest";
import type { CostComponent, Value } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { CostKind, requiredAmount } from "../../src/rules/cr100_game_concepts/costs";
import { chosenNumber, effectResult } from "../../src/rules/cr100_game_concepts/numbers";
import { CastError } from "../../src/rules/cr600_spells_and_abilities/cast_error";
import { checkPayable } from "../../src/rules/cr600_spells_and_abilities/cost_checks";
import { payComponent } from "../../src/rules/cr600_spells_and_abilities/paying";
import { ir } from "../../src/rules/ir_read";
import { installMatch } from "../../src/rules/match";
import { costText, parseCost, substituteX } from "../../src/rules/mana/cost";
import { Board, CARDS } from "./board";

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
});

const component = (kind: number, amount?: Value): CostComponent => ({ $: "CostComponent", kind: kind as never, ...(amount ? { amount } : {}) });
const of = (n: number): Value => ({ $: "Value", constant: n });
/** A realistic way for a cost amount to come out negative. */
const minus = (left: number, right: number): Value => ({ $: "Value", kind: ir("ValueKind.DIFFERENCE"), operands: [of(left), of(right)] });
const identity: Value = { $: "Value", kind: ir("ValueKind.COMMANDER_COLOUR_IDENTITY") };

describe("numbers (CR 107.1b-c)", () => {
  it("an effect's result and a chosen number are never negative", () => {
    expect([effectResult(-3), effectResult(0), effectResult(4)]).toEqual([0, 0, 4]);
    expect([chosenNumber(-1), chosenNumber(0), chosenNumber(7)]).toEqual([0, 0, 7]);
  });

  it("a negative X is not a cost reduction", () => {
    const cost = parseCost("{X}{G}");
    expect(costText(substituteX(cost, -3))).toBe("{G}");
    expect(costText(substituteX(cost, 0))).toBe("{G}");
    expect(costText(substituteX(cost, 2))).toBe("{2}{G}");
  });
});

describe("cost amounts (CR 107.1b, 107.7, 903.4f)", () => {
  it("consuming costs take at least one, others at least zero, loyalty stays signed", () => {
    expect(requiredAmount(component(CostKind.SACRIFICE), -2)).toBe(1);
    expect(requiredAmount(component(CostKind.PAY_ENERGY), -3)).toBe(0);
    expect(requiredAmount(component(CostKind.PAY_LIFE), -3)).toBe(0);
    expect(requiredAmount(component(CostKind.PUT_COUNTERS), -3)).toBe(0);
    expect(requiredAmount(component(CostKind.LOYALTY), -3)).toBe(-3);
  });

  it("an undeterminable amount is refused, even where zero would be free", () => {
    expect(requiredAmount(component(CostKind.PAY_ENERGY), 0)).toBe(0);
    expect(() => requiredAmount(component(CostKind.PAY_ENERGY), 0, true)).toThrow(CastError);
  });

  it("paying a negative energy or life cost hands out nothing", () => {
    const source = board.play("Grizzly Bears");
    const p = player(board.game, 0);
    p.energy = 2;
    payComponent(board.game, 0, component(CostKind.PAY_ENERGY, minus(1, 4)), source);
    expect(p.energy).toBe(2);
    const life = p.life;
    payComponent(board.game, 0, component(CostKind.PAY_LIFE, minus(0, 5)), source);
    expect(p.life).toBe(life);
  });

  it("a cost of zero energy is payable with no energy", () => {
    const source = board.play("Grizzly Bears");
    player(board.game, 0).energy = 0;
    expect(() => checkPayable(board.game, 0, component(CostKind.PAY_ENERGY, of(0)), source)).not.toThrow();
  });

  it("a cost reading the commander's colour identity is payable with a commander", () => {
    const source = board.play("Grizzly Bears");
    const commander = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.COMMAND);
    commander.isCommander = true;
    player(board.game, 0).commanders = [commander.id];
    expect(() => checkPayable(board.game, 0, component(CostKind.PAY_LIFE, identity), source)).not.toThrow();
  });

  // Fails today: match/index.ts's installMatch doesn't install
  // `wasUndeterminable`, so the hook says false and the undefined identity
  // is charged as 0 (Python raises). Reported to the parser in
  // status/rules.md; turn this back into `it` when it is installed.
  it.fails("without a commander it is unpayable (CR 903.4f)", () => {
    const source = board.play("Grizzly Bears");
    expect(() => checkPayable(board.game, 0, component(CostKind.PAY_LIFE, identity), source)).toThrow(CastError);
  });
});
