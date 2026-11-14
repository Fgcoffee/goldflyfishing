/**
 * The Ring tempts you (CR 701.54). Ported from tests/rules/test_cr701_ring.py;
 * the trigger cases (loot, drain) wait for the triggers to run in a game.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board } from "../../../test/rules/board";
import { Supertype, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { hasSupertype } from "../cr200_parts_of_a_card/characteristics";
import { newCombat, type Combat } from "../cr500_turn_structure/combat";
import { installMatch } from "../match";
import { isARingBearer, ringBearer, sacrificeBlockersAtEndOfCombat, tempt } from "./ring";

let board: Board;
const p0 = () => board.game.players[0]!;

beforeEach(() => {
  board = new Board();
  installMatch();
});

describe("the Ring tempts you (CR 701.54)", () => {
  it("gives the emblem once, however often you're tempted, and counts each time", () => {
    const kinds: number[] = [];
    board.game.observer = (_g, e) => void kinds.push(e.kind);
    tempt(board.game, 0);
    tempt(board.game, 0);
    expect(board.game.command.length).toBe(1);
    expect(board.game.objects.get(p0().ringEmblem)!.zone).toBe(Zone.COMMAND);
    expect(p0().ringTemptedCount).toBe(2);
    expect(kinds.filter((k) => k === EventKind.RING_TEMPTED).length).toBe(2);
  });

  it("tempted with no creature still counts (CR 701.54d)", () => {
    tempt(board.game, 0);
    expect(p0().ringTemptedCount).toBe(1);
    expect(ringBearer(board.game, 0)).toBeNull();
  });

  it("chooses the greatest power by default; the agent may choose", () => {
    board.play("Grizzly Bears", 0);
    const angel = board.play("Serra Angel", 0);
    tempt(board.game, 0);
    expect(ringBearer(board.game, 0)).toBe(angel);
    const bears = board.game.battlefield.map((id) => board.game.objects.get(id)!).find((o) => o !== angel)!;
    board.game.agents.set(0, { chooseRingBearer: () => bears });
    tempt(board.game, 0);
    expect(ringBearer(board.game, 0)).toBe(bears);
  });

  it("your Ring-bearer is legendary, and only it", () => {
    const bears = board.play("Grizzly Bears", 0);
    const angel = board.play("Serra Angel", 0);
    tempt(board.game, 0);
    board.refresh();
    expect(hasSupertype(board.chars(angel), Supertype.LEGENDARY)).toBe(true);
    expect(hasSupertype(board.chars(bears), Supertype.LEGENDARY)).toBe(false);
    expect(isARingBearer(board.game, angel)).toBe(true);
  });

  it("losing control ends the designation, for good", () => {
    const angel = board.play("Serra Angel", 0);
    tempt(board.game, 0);
    angel.baseController = 1;
    angel.controller = 1;
    expect(ringBearer(board.game, 0)).toBeNull();
    angel.baseController = 0;
    angel.controller = 0;
    expect(ringBearer(board.game, 0)).toBeNull();
  });

  it("blockers of the Ring-bearer are sacrificed at end of combat (a delayed trigger)", () => {
    const angel = board.play("Serra Angel", 0);
    const blocker = board.play("Grizzly Bears", 1);
    const combat: Combat = newCombat();
    combat.blockers.set(angel.id, [blocker.id]);
    board.game.combat = combat;
    const delayed = sacrificeBlockersAtEndOfCombat(board.game, angel.id, 0)!;
    expect(board.game.delayedTriggers).toContain(delayed);
    expect(delayed.effects[0]!.targets!.specific).toEqual([blocker.id]);
    expect(sacrificeBlockersAtEndOfCombat(board.game, blocker.id, 0)).toBeNull(); // nothing blocks it
  });
});
