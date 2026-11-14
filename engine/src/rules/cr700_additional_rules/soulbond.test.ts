/**
 * Soulbond (CR 702.95). Ported from tests/rules/test_cr702_soulbond.py,
 * calling pair() where Python resolves a PAIR effect (that executor is the
 * rules session's).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, filter } from "../../../test/rules/board";
import type { Condition } from "../../ir";
import { Zone } from "../../kernel/enums";
import type { GameObject } from "../../kernel/gameobject";
import { NO_OBJECT } from "../../kernel/ids";
import { ir } from "../ir_read";
import { find, holds, installMatch } from "../match";
import { moveObject } from "../zones";
import { checkStateBasedActions } from "./sba";
import { endBrokenPairs, isStillPaired, pair, partnerOf } from "./soulbond";

const PAIRED: Condition = { $: "Condition", kind: ir("ConditionKind.IS_PAIRED") } as Condition;
const THE_PAIR = filter({ in_pair_with_source: true });

let board: Board;
let soul: GameObject;
let other: GameObject;
beforeEach(() => {
  board = new Board();
  installMatch();
  soul = board.play("Grizzly Bears", 0);
  other = board.play("Serra Angel", 0);
});

describe("soulbond (CR 702.95)", () => {
  it("pairs two creatures, and the pair is both of them", () => {
    expect(pair(board.game, soul, other, 0)).toBe(true);
    expect(partnerOf(board.game, soul)).toBe(other);
    expect(partnerOf(board.game, other)).toBe(soul);
    expect(holds(board.game, PAIRED, { source: soul.id, controller: 0 })).toBe(true);
    expect(new Set(find(board.game, THE_PAIR, { source: soul.id, controller: 0 }).map((o) => o.id))).toEqual(new Set([soul.id, other.id]));
  });

  it("with nothing paired, the pair is nobody", () => {
    expect(find(board.game, THE_PAIR, { source: soul.id, controller: 0 })).toEqual([]);
    expect(isStillPaired(board.game, soul)).toBe(false);
  });

  it("a creature pairs with only one, and not with itself", () => {
    const third = board.play("Grizzly Bears", 0);
    expect(pair(board.game, soul, soul, 0)).toBe(false);
    pair(board.game, soul, other, 0);
    expect(pair(board.game, third, soul, 0)).toBe(false);
  });

  it("leaving the battlefield ends the pair, cleared at the next check", () => {
    pair(board.game, soul, other, 0);
    moveObject(board.game, other, Zone.GRAVEYARD);
    expect(isStillPaired(board.game, soul)).toBe(false);
    checkStateBasedActions(board.game);
    expect(soul.pairedWith).toBe(NO_OBJECT);
  });

  it("another player gaining control ends it (CR 702.95e)", () => {
    pair(board.game, soul, other, 0);
    other.baseController = 1;
    other.controller = 1;
    board.refresh();
    expect(endBrokenPairs(board.game)).toBe(true);
    expect(soul.pairedWith).toBe(NO_OBJECT);
  });

  it("can't form across controllers", () => {
    const theirs = board.play("Grizzly Bears", 1);
    expect(pair(board.game, soul, theirs, 0)).toBe(false);
  });
});
