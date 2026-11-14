/**
 * Dungeons and venturing (CR 309, 701.49, 704.5t). Ported from
 * tests/rules/test_cr309_dungeons.py, on the dungeons the IR export ships
 * (ir/dungeons.ts). The trigger-resolution cases wait for the triggers port.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board } from "../../../test/rules/board";
import { DUNGEONS } from "../../ir/dungeons";
import { Zone } from "../../kernel/enums";
import { EventKind, type GameEvent } from "../../kernel/events";
import { NO_OBJECT } from "../../kernel/ids";
import { ir, num } from "../ir_read";
import { installMatch } from "../match";
import { checkStateBasedActions } from "../cr700_additional_rules/sba";
import { availableDungeons, currentDungeon, definitionOf, hasQuality, venture } from "./dungeons";

let board: Board;
let events: GameEvent[];
const p0 = () => board.game.players[0]!;
const lostMine = () => DUNGEONS.find((d) => d.name === "Lost Mine of Phandelver")!;

beforeEach(() => {
  board = new Board();
  installMatch();
  events = [];
  board.game.observer = (_g, e) => void events.push(e);
});

describe("the exported dungeons", () => {
  it("have room graphs read from the oracle text", () => {
    const mine = lostMine();
    expect(mine.mappable).toBe(true);
    expect(mine.rooms.length).toBe(7);
    expect(mine.rooms[0]!.leads_to.length).toBe(2); // Cave Entrance forks
    expect(mine.rooms[mine.bottommost]!.leads_to).toEqual([]);
    expect(mine.abilities.length).toBe(mine.rooms.length);
  });

  it("a dungeon printed without arrows is not offered", () => {
    const baldur = DUNGEONS.find((d) => d.name === "Baldur's Gate Wilderness");
    expect(baldur?.mappable).toBe(false);
  });

  it("room abilities trigger on the marker moving, from the command zone", () => {
    const ability = lostMine().abilities[0]!;
    expect(num(ability.kind)).toBe(ir("AbilityKind.TRIGGERED"));
    expect(ability.trigger?.room).toBe(1);
  });

  it("Undercity is entered only by its quality (CR 701.49d)", () => {
    const undercity = DUNGEONS.find((d) => d.name === "Undercity")!;
    expect(undercity.entry_quality).toBe("Undercity");
    expect(hasQuality(undercity, "undercity")).toBe(true);
  });
});

describe("venturing (CR 701.49)", () => {
  it("the first venture brings a dungeon into the command zone, marker on the topmost room", () => {
    venture(board.game, 0);
    const dungeon = currentDungeon(board.game, 0)!;
    expect(dungeon.zone).toBe(Zone.COMMAND);
    expect(definitionOf(dungeon)!.name).toBe("Lost Mine of Phandelver"); // the rules' order, no agent
    expect(p0().ventureRoom).toBe(0);
    const moved = events.find((e) => e.kind === EventKind.VENTURE_MARKER_MOVED)!;
    expect(moved.amount).toBe(1);
    expect(board.chars(dungeon).abilities.length).toBe(7);
  });

  it("the player chooses at a fork; the default is the first arrow", () => {
    board.game.agents.set(0, { chooseDungeonRoom: () => 1 });
    venture(board.game, 0);
    venture(board.game, 0);
    expect(p0().ventureRoom).toBe(lostMine().rooms[0]!.leads_to[1]);
  });

  it("one dungeon at a time", () => {
    venture(board.game, 0);
    venture(board.game, 0);
    expect(board.game.command.length).toBe(1);
  });

  it("venture into Undercity enters only Undercity", () => {
    venture(board.game, 0, "Undercity");
    expect(definitionOf(currentDungeon(board.game, 0)!)!.name).toBe("Undercity");
  });

  it("from the bottommost room, venturing completes it and starts again (CR 701.49c)", () => {
    venture(board.game, 0);
    p0().ventureRoom = lostMine().bottommost;
    venture(board.game, 0);
    expect(p0().completedDungeons).toEqual(["Lost Mine of Phandelver"]);
    expect(currentDungeon(board.game, 0)).not.toBeNull();
    expect(p0().ventureRoom).toBe(0);
  });

  it("with no dungeon to use, nothing happens", () => {
    board.game.players[0]!.hasLost = false;
    (board.game as unknown as { dungeons: unknown[] }).dungeons = [];
    venture(board.game, 0);
    expect(p0().dungeon).toBe(NO_OBJECT);
    expect(availableDungeons(board.game)).toEqual([]);
  });
});

describe("completing a dungeon (CR 704.5t, 309.7)", () => {
  it("is completed at the bottom once no room ability waits", () => {
    venture(board.game, 0);
    const dungeon = currentDungeon(board.game, 0)!;
    p0().ventureRoom = lostMine().bottommost;
    board.game.pendingTriggers.push({ source: dungeon.id });
    checkStateBasedActions(board.game);
    expect(currentDungeon(board.game, 0)).not.toBeNull(); // its last room ability is waiting
    board.game.pendingTriggers.length = 0;
    checkStateBasedActions(board.game);
    expect(currentDungeon(board.game, 0)).toBeNull();
    expect(p0().completedDungeons).toEqual(["Lost Mine of Phandelver"]);
    expect(events.some((e) => e.kind === EventKind.DUNGEON_COMPLETED)).toBe(true);
  });

  it("a dungeon short of the bottom is not completed", () => {
    venture(board.game, 0);
    checkStateBasedActions(board.game);
    expect(currentDungeon(board.game, 0)).not.toBeNull();
  });
});
