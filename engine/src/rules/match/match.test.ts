// Ported from tests/rules/test_cr108_owner_filters.py, test_object_filter_any_of.py,
// test_filter_bounds.py and test_history_conditions.py, plus value cases from values.py.
import { describe, expect, it } from "vitest";
import { Board, CARDS, constant, filter } from "../../../test/rules/board";
import type { Condition, ObjectFilter, PlayerFilter, Value } from "../../ir";
import { CardType, Color, Zone } from "../../kernel/enums";
import { createObject, turnHistoryCountKey, turnHistoryKey } from "../../kernel/game";
import { addCounters } from "../../kernel/gameobject";
import { Comparison, ConditionKind, ControllerRelation, PlayerScope, ValueKind } from "./enums";
import { evaluate, find, holds, installMatch, matches, resolvePlayers } from "./index";
import { matchHooks } from "../../kernel/hooks";

const value = (fields: Partial<Value>): Value => ({ $: "Value", ...fields }) as Value;
const players = (scope: number): PlayerFilter => ({ $: "PlayerFilter", scope }) as PlayerFilter;
const condition = (fields: Partial<Condition>): Condition => ({ $: "Condition", ...fields }) as Condition;
const bound = (comparison: number, v: Value) => ({ $: "NumericConstraint", comparison, value: v }) as never;
const YOURS = filter({ owner: ControllerRelation.YOU } as Partial<ObjectFilter>);
const THEIRS = filter({ owner: ControllerRelation.OPPONENT } as Partial<ObjectFilter>);
const names = (board: Board, objs: { id: number }[]) => objs.map((o) => board.chars(o as never).name).sort();

function bury(board: Board, name: string, owner: number) {
  const obj = createObject(board.game, CARDS[name]!, owner, Zone.GRAVEYARD);
  board.refresh();
  return obj;
}

describe("owner filters (CR 108.3)", () => {
  it("your own object matches, someone else's does not", () => {
    const board = new Board();
    const mine = board.play("Grizzly Bears", 0);
    const theirs = board.play("Serra Angel", 1);
    expect(matches(board.game, mine, YOURS, { controller: 0 })).toBe(true);
    expect(matches(board.game, theirs, YOURS, { controller: 0 })).toBe(false);
    expect(matches(board.game, theirs, THEIRS, { controller: 0 })).toBe(true);
    expect(matches(board.game, mine, THEIRS, { controller: 0 })).toBe(false);
    expect(matches(board.game, theirs, filter({}), { controller: 0 })).toBe(true);
  });

  it("owner is not controller", () => {
    const board = new Board();
    const stolen = board.play("Serra Angel", 1);
    stolen.controller = 0;
    expect(matches(board.game, stolen, filter({ controller: ControllerRelation.YOU } as Partial<ObjectFilter>), { controller: 0 })).toBe(true);
    expect(matches(board.game, stolen, YOURS, { controller: 0 })).toBe(false);
  });

  it("your graveyard is yours alone; an unowned zone filter reaches both", () => {
    const board = new Board();
    bury(board, "Grizzly Bears", 0);
    bury(board, "Serra Angel", 1);
    const graveyard = { $set: [Zone.GRAVEYARD] };
    expect(names(board, find(board.game, filter({ zones: graveyard, owner: ControllerRelation.YOU } as Partial<ObjectFilter>), { controller: 0 }))).toEqual(["Grizzly Bears"]);
    expect(names(board, find(board.game, filter({ zones: graveyard } as Partial<ObjectFilter>), { controller: 0 }))).toEqual(["Grizzly Bears", "Serra Angel"]);
  });
});

describe("any_of", () => {
  it("either alternative matches, and the other fields still apply", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 0);
    const forest = board.play("Forest", 0);
    const anthem = board.play("Glorious Anthem", 0);
    const theirBears = board.play("Grizzly Bears", 1);
    const spec = filter({
      controller: ControllerRelation.YOU,
      any_of: [filter({ types_all: CardType.CREATURE }), filter({ subtypes_any: ["Forest"] })],
    } as Partial<ObjectFilter>);
    const found = find(board.game, spec, { controller: 0 }).map((o) => o.id);
    expect(found).toContain(bears.id);
    expect(found).toContain(forest.id);
    expect(found).not.toContain(anthem.id);
    expect(found).not.toContain(theirBears.id);
  });
});

describe("numeric bounds (test_filter_bounds.py)", () => {
  it("X is read off the spell, not off the candidate", () => {
    const board = new Board();
    const elves = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.LIBRARY);
    const spell = createObject(board.game, CARDS["Forest"]!, 0, Zone.HAND);
    board.refresh();
    spell.xValue = 2;
    const spec = filter({ zones: { $set: [Zone.LIBRARY] }, mana_value: bound(Comparison.LE, value({ kind: ValueKind.X })) } as Partial<ObjectFilter>);
    expect(matches(board.game, elves, spec, { source: spell.id, controller: 0 })).toBe(true);
    spell.xValue = 1;
    expect(matches(board.game, elves, spec, { source: spell.id, controller: 0 })).toBe(false);
  });

  it("a counted bound uses the ability controller's board", () => {
    const board = new Board();
    const source = board.play("Grizzly Bears", 0);
    board.play("Grizzly Bears", 0);
    board.play("Grizzly Bears", 0);
    const angel = board.play("Serra Angel", 1); // mana value 5... power 4
    const spec = filter({
      power: bound(Comparison.LE, value({ kind: ValueKind.COUNT, filter: filter({ types_all: CardType.CREATURE, controller: ControllerRelation.YOU } as Partial<ObjectFilter>) })),
    } as Partial<ObjectFilter>);
    // Three creatures you control: power 4 is too much; as the opponent's count (1) it would be too.
    expect(matches(board.game, angel, spec, { source: source.id, controller: 0 })).toBe(false);
    board.play("Grizzly Bears", 0);
    expect(matches(board.game, angel, spec, { source: source.id, controller: 0 })).toBe(true);
  });

  it("an of_affected bound reads the candidate; one mixing both subjects fails closed; one needing an ability with none fails", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 0);
    const source = board.play("Serra Angel", 0);
    const itsPower = value({ kind: ValueKind.POWER, of_affected: true });
    expect(matches(board.game, bears, filter({ toughness: bound(Comparison.GE, itsPower) } as Partial<ObjectFilter>), { source: source.id })).toBe(true);
    const mixed = value({ kind: ValueKind.SUM, operands: [itsPower, value({ kind: ValueKind.POWER })] });
    expect(matches(board.game, bears, filter({ toughness: bound(Comparison.GE, mixed) } as Partial<ObjectFilter>), { source: source.id })).toBe(false);
    expect(matches(board.game, bears, filter({ mana_value: bound(Comparison.LE, value({ kind: ValueKind.X })) } as Partial<ObjectFilter>), {})).toBe(false);
    // Arithmetic over constants needs no ability.
    const two = value({ kind: ValueKind.SUM, operands: [constant(1), constant(1)] });
    expect(matches(board.game, bears, filter({ power: bound(Comparison.EQ, two) } as Partial<ObjectFilter>), {})).toBe(true);
  });
});

describe("values", () => {
  it("counts, superlatives, devotion and arithmetic", () => {
    const board = new Board();
    board.play("Grizzly Bears", 0);
    board.play("Serra Angel", 0);
    board.play("Forest", 0);
    const creatures = filter({ types_all: CardType.CREATURE, controller: ControllerRelation.YOU } as Partial<ObjectFilter>);
    expect(evaluate(board.game, value({ kind: ValueKind.COUNT, filter: creatures }), { controller: 0 })).toBe(2);
    const greatestPower = value({ kind: ValueKind.GREATEST_AMONG, filter: creatures, operands: [value({ kind: ValueKind.POWER })] });
    expect(evaluate(board.game, greatestPower, { controller: 0 })).toBe(4);
    const total = value({ kind: ValueKind.TOTAL_AMONG, filter: creatures, operands: [value({ kind: ValueKind.MANA_VALUE })] });
    expect(evaluate(board.game, total, { controller: 0 })).toBe(7);
    // Serra Angel {3}{W}{W}: devotion to white is 2.
    expect(evaluate(board.game, value({ kind: ValueKind.DEVOTION, colors: Color.WHITE }), { controller: 0 })).toBe(2);
    // Python floor division: half of -3 rounded down is -2.
    const half = value({ kind: ValueKind.HALF_ROUNDED_DOWN, operands: [constant(-3)] });
    expect(evaluate(board.game, half, {})).toBe(-2);
    expect(evaluate(board.game, value({ kind: ValueKind.HALF_ROUNDED_UP, operands: [constant(5)] }), {})).toBe(3);
    expect(evaluate(board.game, value({ kind: ValueKind.LIFE_TOTAL, players: players(PlayerScope.YOU) }), { controller: 0 })).toBe(
      board.game.players[0]!.life,
    );
  });

  it("a remembered filter names only what the resolution acted on", () => {
    const board = new Board();
    const a = board.play("Grizzly Bears", 0);
    board.play("Grizzly Bears", 0);
    const destroyed = filter({ types_all: CardType.CREATURE, remembered: true } as Partial<ObjectFilter>);
    expect(evaluate(board.game, value({ kind: ValueKind.COUNT, filter: destroyed }), { remembered: [a.id] })).toBe(1);
  });
});

describe("conditions", () => {
  it("controls-matching, and/or/not, your turn", () => {
    const board = new Board();
    board.play("Forest", 0);
    const controlsForest = condition({
      kind: ConditionKind.CONTROLS_MATCHING,
      filter: filter({ subtypes_any: ["Forest"], controller: ControllerRelation.YOU } as Partial<ObjectFilter>),
      constraint: bound(Comparison.GE, constant(1)),
    });
    expect(holds(board.game, controlsForest, { controller: 0 })).toBe(true);
    expect(holds(board.game, controlsForest, { controller: 1 })).toBe(false);
    expect(holds(board.game, condition({ kind: ConditionKind.NOT, operands: [controlsForest] }), { controller: 1 })).toBe(true);
    board.game.activePlayer = 0;
    expect(holds(board.game, condition({ kind: ConditionKind.IS_YOUR_TURN }), { controller: 0 })).toBe(true);
    expect(holds(board.game, condition({ kind: ConditionKind.IS_YOUR_TURN }), { controller: 1 })).toBe(false);
  });

  it("an opponent is some one opponent; each opponent is all of them (CR 102.3)", () => {
    const board = new Board();
    board.game.players[1]!.life = 8;
    const low = (scope: number) =>
      condition({ kind: ConditionKind.LIFE, players: players(scope), constraint: bound(Comparison.LE, constant(10)) });
    expect(holds(board.game, low(PlayerScope.OPPONENT), { controller: 0 })).toBe(true);
    expect(holds(board.game, low(PlayerScope.EACH_OPPONENT), { controller: 0 })).toBe(true);
    expect(holds(board.game, low(PlayerScope.YOU), { controller: 0 })).toBe(false);
  });

  it("this turn's history: a count and an amount are different questions", () => {
    const board = new Board();
    const LIFE_LOST = 7;
    board.game.turnHistory.set(turnHistoryKey(LIFE_LOST, 1), 5);
    board.game.turnHistory.set(turnHistoryCountKey(LIFE_LOST, 1), 2);
    const asked = (counterType: string, n: number) =>
      condition({
        kind: ConditionKind.EVENT_THIS_TURN,
        players: players(PlayerScope.OPPONENT),
        event_kinds: [LIFE_LOST],
        counter_type: counterType,
        constraint: bound(Comparison.GE, constant(n)),
      });
    expect(holds(board.game, asked("", 5), { controller: 0 })).toBe(true);
    expect(holds(board.game, asked("count", 3), { controller: 0 })).toBe(false);
    expect(holds(board.game, asked("count", 2), { controller: 0 })).toBe(true);
    expect(holds(board.game, asked("", 1), { controller: 1 })).toBe(false); // the wrong player
  });

  it("'if it was a creature' is asked of what the resolution acted on, from last-known information", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 0);
    const itWasACreature = condition({
      kind: ConditionKind.REMEMBERED_MATCHES,
      filter: filter({ types_all: CardType.CREATURE, zones: { $set: [] } } as Partial<ObjectFilter>),
    });
    expect(holds(board.game, itWasACreature, {})).toBe(false); // nothing to ask about
    expect(holds(board.game, itWasACreature, { remembered: [bears.id] })).toBe(true);
    board.remove(bears);
    expect(holds(board.game, itWasACreature, { remembered: [bears.id] })).toBe(true);
  });

  it("counters on the source", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 0);
    addCounters(bears, "+1/+1", 2);
    const twoOrMore = condition({ kind: ConditionKind.COUNTER_COUNT, counter_type: "+1/+1", constraint: bound(Comparison.GE, constant(2)) });
    expect(holds(board.game, twoOrMore, { source: bears.id })).toBe(true);
  });
});

describe("players and the hooks", () => {
  it("resolves player filters in APNAP order", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    expect(resolvePlayers(board.game, players(PlayerScope.EACH_OPPONENT), { controller: 0 })).toEqual([1]);
    expect(resolvePlayers(board.game, players(PlayerScope.YOU), { controller: 1 })).toEqual([1]);
  });

  it("installMatch puts matches, find, evaluate, holds and valueSubjects into the hooks", () => {
    const board = new Board(); // installs the layer tests' stand-in matcher
    installMatch();
    const bears = board.play("Grizzly Bears", 0);
    expect(matchHooks().matches(board.game, bears, YOURS, { controller: 1 })).toBe(false);
  });

  it("installMatch puts resolvePlayers and defendingPlayers into the hooks", () => {
    const board = new Board();
    installMatch();
    board.game.activePlayer = 0;
    expect(matchHooks().resolvePlayers(board.game, players(PlayerScope.EACH_OPPONENT), { controller: 0 })).toEqual([1]);
    const bears = board.play("Grizzly Bears", 0);
    expect(matchHooks().defendingPlayers(board.game, bears.id)).toEqual([]); // not attacking
  });
});
