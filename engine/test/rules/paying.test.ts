// Ported from the payment tests of tests/rules/test_cr601_casting.py,
// test_mana_plan.py and test_cr602_activation.py: activating mana
// abilities to pay (CR 601.2g, 605.3b), activation costs, player-scoped
// costs, convoke's matching, and the F61 guard.
import { describe, expect, it } from "vitest";
import type { Ability, CostComponent, Effect } from "../../src/ir";
import { Color, Zone } from "../../src/kernel/enums";
import { EventKind, type GameEvent } from "../../src/kernel/events";
import { createObject, player } from "../../src/kernel/game";
import { CostKind, newTotalCost } from "../../src/rules/cr100_game_concepts/costs";
import { CastError } from "../../src/rules/cr600_spells_and_abilities/cast_error";
import { executePlanFor, manaOptions, planPaymentFor } from "../../src/rules/cr600_spells_and_abilities/mana_host";
import {
  activationLimitReached,
  canPayActivation,
  canPayCost,
  colourMatching,
  pay,
  payActivation,
  payComponent,
  payCost,
  recordActivation,
} from "../../src/rules/cr600_spells_and_abilities/paying";
import { ir } from "../../src/rules/ir_read";
import { parseCost } from "../../src/rules/mana/cost";
import { poolTotal } from "../../src/rules/mana/pool";
import { ALL_CREATURES, Board, CARDS, effect, mana } from "./board";

const component = (kind: number, fields: Partial<CostComponent> = {}): CostComponent => ({ $: "CostComponent", kind: kind as never, ...fields });
const addMana = (...symbols: string[]): Effect => effect({ kind: ir("EffectKind.ADD_MANA"), mana_produced: symbols });
const tapFor = (...symbols: string[]): Ability => ({
  $: "Ability", kind: ir("AbilityKind.ACTIVATED"), is_mana_ability: true,
  cost: { $: "Cost", components: [component(CostKind.TAP_SELF)] }, effects: [addMana(...symbols)], text: `{T}: Add ${symbols.join("")}.`,
});
const pump = (generic: number, ...colours: Color[]): Ability => ({
  $: "Ability", kind: ir("AbilityKind.ACTIVATED"),
  cost: { $: "Cost", components: [component(CostKind.MANA, { mana: mana(generic, ...colours) })] }, effects: [], text: "pump",
});

function forests(board: Board, n: number) {
  board.script("Forest", tapFor("{G}"));
  return Array.from({ length: n }, () => board.play("Forest"));
}

function watch(board: Board): GameEvent[] {
  const seen: GameEvent[] = [];
  board.game.observer = (_g, e) => void seen.push(e);
  return seen;
}

describe("mana options (mana_plan.mana_options)", () => {
  it("each untapped source with what it would add; a tapped one is not offered", () => {
    const board = new Board();
    const [a, b] = forests(board, 2);
    b!.tapped = true;
    const options = manaOptions(board.game, 0);
    expect(options).toHaveLength(1);
    expect(options[0]![0]!.source).toBe(a!.id);
    expect(options[0]![0]!.produces.map((k) => k.color)).toEqual([Color.GREEN]);
  });
});

describe("activation costs (CR 602.2b, 605.3b)", () => {
  it("a {G} activation taps a Forest for it, through the planner", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    board.script("Grizzly Bears", pump(0, Color.GREEN));
    const bears = board.play("Grizzly Bears");
    const ability = board.chars(bears).abilities[0]!;
    expect(canPayActivation(board.game, bears, ability)).toBe(true);
    const seen = watch(board);
    payActivation(board.game, bears, ability);
    expect(forest!.tapped).toBe(true);
    expect(poolTotal(player(board.game, 0).manaPool)).toBe(0);
    expect(seen.filter((e) => e.kind === EventKind.TAPPED_FOR_MANA).map((e) => e.objectId)).toEqual([forest!.id]);
    expect(canPayActivation(board.game, bears, ability)).toBe(false);
    expect(() => payActivation(board.game, bears, ability)).toThrow(CastError);
  });

  it("{T} needs an untapped creature that isn't summoning sick (CR 302.6)", () => {
    const board = new Board();
    board.script("Grizzly Bears", tapFor("{G}"));
    const bears = board.play("Grizzly Bears");
    const ability = board.chars(bears).abilities[0]!;
    bears.summoningSick = true;
    expect(canPayActivation(board.game, bears, ability)).toBe(false);
    bears.summoningSick = false;
    expect(canPayActivation(board.game, bears, ability)).toBe(true);
  });

  it("once each turn (CR 602.5b)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const once: Ability = { ...pump(0), once_each_turn: true };
    expect(activationLimitReached(bears, once, 0)).toBe(false);
    recordActivation(bears, once, 0);
    expect(activationLimitReached(bears, once, 0)).toBe(true);
  });
});

describe("paying for a spell (CR 601.2g-h)", () => {
  it("taps what it needs and spends the mana", () => {
    const board = new Board();
    const lands = forests(board, 3);
    const spell = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    pay(board.game, 0, newTotalCost({ base: parseCost("{1}{G}") }), spell);
    expect(lands.filter((l) => l.tapped)).toHaveLength(2);
    expect(poolTotal(player(board.game, 0).manaPool)).toBe(0);
  });

  it("nothing is spent when a non-mana part can't be paid", () => {
    const board = new Board();
    const lands = forests(board, 2);
    const spell = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    const sacrifice = component(CostKind.SACRIFICE, { filter: ALL_CREATURES, amount: { $: "Value", constant: 1 } });
    expect(() => pay(board.game, 0, newTotalCost({ base: parseCost("{G}"), additional: [sacrifice] }), spell)).toThrow(CastError);
    expect(lands.some((l) => l.tapped)).toBe(false);
  });

  it("a sacrifice records what it consumed (CR 608.2h)", () => {
    const board = new Board();
    const victim = board.play("Grizzly Bears");
    const spell = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.HAND);
    payComponent(board.game, 0, component(CostKind.SACRIFICE, { filter: ALL_CREATURES, amount: { $: "Value", constant: 1 } }), spell);
    expect(spell.costPaidObjects).toEqual([victim.id]);
    expect(board.game.costPaidLastKnown.has(victim.id)).toBe(true);
  });

  it("a plan whose source is gone rewinds the cast (F61)", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    const plan = planPaymentFor(board.game, 0, parseCost("{G}"))!;
    expect(plan).toHaveLength(1);
    board.remove(forest!);
    expect(() => executePlanFor(board.game, 0, plan)).toThrow(CastError);
  });
});

describe("costs charged to a player", () => {
  it("'unless that player pays {1}': paid from an untapped source, refused without one", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    const one = { $: "Cost" as const, components: [component(CostKind.MANA, { mana: mana(1) })] };
    expect(canPayCost(board.game, 0, one)).toBe(true);
    expect(payCost(board.game, 0, one)).toBe(true);
    expect(forest!.tapped).toBe(true);
    expect(canPayCost(board.game, 0, one)).toBe(false);
    expect(payCost(board.game, 0, one)).toBe(false);
  });
});

describe("convoke's matching (CR 702.51a)", () => {
  it("gives the one-colour creature the pip only it can pay", () => {
    expect(colourMatching([Color.WHITE | Color.BLUE, Color.WHITE], [Color.WHITE, Color.BLUE])).toEqual([1, 0]);
    expect(colourMatching([Color.RED], [Color.WHITE])).toEqual([-1]);
  });
});

