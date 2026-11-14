/**
 * Conditions the board can't answer: "this turn" and "it". Ported from
 * tests/rules/test_history_conditions.py (the board cases; the grammar
 * cases stay in Python).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board } from "../../../test/rules/board";
import type { Condition, ObjectFilter } from "../../ir";
import { CardType, Color, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { loseLife } from "../cr100_game_concepts/actions";
import { takeTurn } from "../cr500_turn_structure/turn";
import { moveObject } from "../zones";
import { ConditionKind, PlayerScope } from "./enums";
import { holds, installMatch } from "./index";

const atLeast = (n: number) => ({ $: "NumericConstraint", comparison: 5 /* GE */, value: { $: "Value", constant: n } }) as never;
const lostTwoLife = (fields: Partial<Condition> = {}): Condition =>
  ({
    $: "Condition",
    kind: ConditionKind.EVENT_THIS_TURN,
    players: { $: "PlayerFilter", scope: PlayerScope.EACH_OPPONENT },
    constraint: atLeast(2),
    event_kinds: [EventKind.LIFE_LOST],
    ...fields,
  }) as Condition;
const remembered = (fields: Partial<ObjectFilter>): Condition =>
  ({ $: "Condition", kind: ConditionKind.REMEMBERED_MATCHES, filter: { $: "ObjectFilter", remembered: true, zones: { $set: [] }, ...fields } }) as Condition;

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
});

describe("'this turn' (EVENT_THIS_TURN)", () => {
  it("nothing has happened yet", () => {
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(false);
  });

  it("an amount accumulates across the turn", () => {
    loseLife(board.game, 1, 1);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(false);
    loseLife(board.game, 1, 1);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(true);
  });

  it("the history clears at the start of a turn", () => {
    loseLife(board.game, 1, 5);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(true);
    takeTurn(board.game);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(false);
  });

  it("the wrong player doesn't count", () => {
    loseLife(board.game, 0, 5);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(false);
  });

  it("a count and an amount are different questions", () => {
    loseLife(board.game, 1, 5);
    expect(holds(board.game, lostTwoLife(), { controller: 0 })).toBe(true);
    expect(holds(board.game, lostTwoLife({ counter_type: "count" }), { controller: 0 })).toBe(false);
  });
});

describe("'it' (REMEMBERED_MATCHES)", () => {
  it("without being told what 'it' is, there's no answer", () => {
    board.play("Grizzly Bears");
    expect(holds(board.game, remembered({ types_all: CardType.CREATURE }), { controller: 0 })).toBe(false);
  });

  it("matches when the description fits, and not otherwise", () => {
    const bear = board.play("Grizzly Bears");
    expect(holds(board.game, remembered({ types_all: CardType.CREATURE }), { controller: 0, remembered: [bear.id] })).toBe(true);
    expect(holds(board.game, remembered({ types_all: CardType.ARTIFACT }), { controller: 0, remembered: [bear.id] })).toBe(false);
  });

  it("is answered from last-known information (CR 608.2g)", () => {
    const bear = board.play("Grizzly Bears");
    const dead = moveObject(board.game, bear, Zone.GRAVEYARD);
    board.refresh();
    expect(holds(board.game, remembered({ types_all: CardType.CREATURE }), { controller: 0, remembered: [dead.id] })).toBe(true);
  });

  it("a colour test", () => {
    const bear = board.play("Grizzly Bears");
    expect(holds(board.game, remembered({ colors_any: Color.GREEN }), { controller: 0, remembered: [bear.id] })).toBe(true);
    expect(holds(board.game, remembered({ colors_any: Color.BLUE }), { controller: 0, remembered: [bear.id] })).toBe(false);
  });
});
