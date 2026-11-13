// Ported from cr118_costs.py and the cost parts of cr601_casting.py
// (tests/rules/test_cr118_costs.py, test_cr601_casting.py): CR 601.2f order,
// floors, additional-cost payments, the payability dry run, crew power.
import { describe, expect, it } from "vitest";
import type { Ability, CostComponent } from "../../src/ir";
import { createObject, player } from "../../src/kernel/game";
import { Color, Zone } from "../../src/kernel/enums";
import { CostKind, finalMana, newTotalCost, requiredAmount } from "../../src/rules/cr100_game_concepts/costs";
import { action, ActionKind } from "../../src/rules/cr100_game_concepts/priority";
import { CastError } from "../../src/rules/cr600_spells_and_abilities/cast_error";
import { checkPayable, creaturesWithTotalPower } from "../../src/rules/cr600_spells_and_abilities/cost_checks";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { computeTotalCost, recordedPayments } from "../../src/rules/cr600_spells_and_abilities/total_cost";
import { ir } from "../../src/rules/ir_read";
import { costColors, genericAmount, manaValue, parseCost, type ManaCost } from "../../src/rules/mana/cost";

/** Generic amount and colours: what the order of CR 601.2f changes. */
const shape = (c: ManaCost) => [genericAmount(c), costColors(c), manaValue(c)];
import { ALL_CREATURES, Board, CARDS, effect, filter, staticAbility } from "./board";

const component = (kind: number, fields: Partial<CostComponent> = {}): CostComponent => ({ $: "CostComponent", kind: kind as never, ...fields });
const constant = (n: number) => ({ $: "Value" as const, constant: n });

describe("total cost (CR 601.2f)", () => {
  it("X first, then increases, then reductions, which never eat coloured mana", () => {
    expect(shape(finalMana(newTotalCost({ base: parseCost("{X}{G}"), xValue: 2, increase: 1, reduction: 1 })))).toEqual([2, Color.GREEN, 3]);
    expect(shape(finalMana(newTotalCost({ base: parseCost("{1}{G}"), reduction: 5 })))).toEqual([0, Color.GREEN, 1]);
  });

  it("consuming costs are at least one; loyalty stays signed; the rest never negative", () => {
    expect(requiredAmount(component(CostKind.SACRIFICE), 0)).toBe(1);
    expect(requiredAmount(component(CostKind.LOYALTY), -3)).toBe(-3);
    expect(requiredAmount(component(CostKind.PAY_ENERGY), -3)).toBe(0);
    expect(() => requiredAmount(component(CostKind.PAY_LIFE), 0, true)).toThrow(CastError);
  });

  it("a spell's mana cost, plus a 'creature spells cost {1} more' tax from a permanent", () => {
    const board = new Board();
    const bears = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    const cast = action(ActionKind.CAST_SPELL, { source: bears.id });
    expect(shape(finalMana(computeTotalCost(board.game, bears, 0, cast)))).toEqual([1, Color.GREEN, 2]);
    board.script("Ghostly Prison", staticAbility("Creature spells cost {1} more.", effect({
      kind: ir("EffectKind.MODIFY_COST"), targets: filter({ ...ALL_CREATURES, zones: { $set: [Zone.STACK] } }), amount: constant(1),
    })));
    board.play("Ghostly Prison");
    const total = computeTotalCost(board.game, bears, 0, cast);
    expect(total.increase).toBe(1);
    expect(shape(finalMana(total))).toEqual([2, Color.GREEN, 3]);
  });

  it("kicker counts once however often announced; multikicker once per announcement", () => {
    const board = new Board();
    const additional = (keyword: string): Ability => ({
      $: "Ability", kind: ir("AbilityKind.STATIC"), keyword,
      additional_cost: { $: "AdditionalCost", optional: true, keyword, cost: { $: "Cost", components: [] } },
    });
    board.script("Grizzly Bears", additional("Kicker"), additional("Multikicker"));
    const bears = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    expect(recordedPayments(characteristicsOf(board.game, bears), [0, 0, 1, 1, 5])).toEqual([0, 1, 1]);
  });
});

describe("the payability dry run (CR 601.2h)", () => {
  it("life, tapping and sacrifice costs are checked without paying", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    player(board.game, 0).life = 3;
    expect(() => checkPayable(board.game, 0, component(CostKind.PAY_LIFE, { amount: constant(4) }), bears)).toThrow("not enough life");
    checkPayable(board.game, 0, component(CostKind.PAY_LIFE, { amount: constant(3) }), bears);
    expect(player(board.game, 0).life).toBe(3);
    checkPayable(board.game, 0, component(CostKind.TAP_SELF), bears);
    bears.tapped = true;
    expect(() => checkPayable(board.game, 0, component(CostKind.TAP_SELF), bears)).toThrow("cannot tap");
    const sacrificeTwo = component(CostKind.SACRIFICE, { filter: ALL_CREATURES, amount: constant(2) });
    board.play("Serra Angel", 1); // not yours to sacrifice (CR 701.21a)
    expect(() => checkPayable(board.game, 0, sacrificeTwo, bears)).toThrow("not enough permanents");
    board.play("Serra Angel", 0);
    checkPayable(board.game, 0, sacrificeTwo, bears);
  });

  it("crew taps the smallest creature enough alone, else the strongest first", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const angel = board.play("Serra Angel");
    const servant = board.play("Painter's Servant");
    expect(creaturesWithTotalPower(board.game, [bears, angel, servant], 2)).toEqual([bears]);
    expect(creaturesWithTotalPower(board.game, [bears, servant], 3)).toEqual([bears, servant]);
    expect(creaturesWithTotalPower(board.game, [bears], 3)).toBeNull();
  });
});
