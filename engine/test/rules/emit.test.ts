// Ported from Game.emit in mtgfish/rules/kernel/game.py and the CR 700
// announcements in cr700_additional_rules/cr700_general.py.
import { describe, expect, it } from "vitest";
import { Zone } from "../../src/kernel/enums";
import { event, EventKind, type GameEvent } from "../../src/kernel/events";
import { createObject, player, turnHistoryCountKey, turnHistoryKey } from "../../src/kernel/game";
import { ObjectKind } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { logDigest } from "../../src/kernel/log";
import { emit, installTriggerCollector } from "../../src/rules/emit";
import { Board, CARDS } from "./board";

function seen(board: Board): GameEvent[] {
  const events: GameEvent[] = [];
  board.game.observer = (_g, e) => events.push(e);
  return events;
}

describe("the event bus", () => {
  it("logs every event into the digest and passes it to the observer and the trigger collector", () => {
    const board = new Board();
    const events = seen(board);
    const collected: number[] = [];
    installTriggerCollector((_g, e) => collected.push(e.kind));
    const before = logDigest(board.game.log);
    emit(board.game, event(EventKind.DREW_CARD, { player: 0 }));
    installTriggerCollector(() => {});
    expect(logDigest(board.game.log)).not.toBe(before);
    expect(events.map((e) => e.kind)).toEqual([EventKind.DREW_CARD]);
    expect(collected).toEqual([EventKind.DREW_CARD]);
  });

  it("tallies this turn's events by kind and player", () => {
    const board = new Board();
    emit(board.game, event(EventKind.LIFE_GAINED, { player: 1, amount: 3 }));
    emit(board.game, event(EventKind.LIFE_GAINED, { player: 1, amount: 2 }));
    emit(board.game, event(EventKind.DREW_CARD, { player: 1 }));
    const h = board.game.turnHistory;
    expect(h.get(turnHistoryKey(EventKind.LIFE_GAINED, 1))).toBe(5);
    expect(h.get(turnHistoryCountKey(EventKind.LIFE_GAINED, 1))).toBe(2);
    expect(h.get(turnHistoryKey(EventKind.DREW_CARD, 1))).toBe(1);
  });

  it("an opponent losing life raises the active player's speed once a turn (CR 702.179d)", () => {
    const board = new Board();
    const game = board.game;
    game.activePlayer = 0;
    player(game, 0).speed = 1;
    const events = seen(board);
    emit(game, event(EventKind.LIFE_LOST, { player: 0, amount: 1 }));
    expect(player(game, 0).speed).toBe(1);
    emit(game, event(EventKind.LIFE_LOST, { player: 1, amount: 1 }));
    emit(game, event(EventKind.LIFE_LOST, { player: 1, amount: 1 }));
    expect(player(game, 0).speed).toBe(2);
    expect(events.filter((e) => e.kind === EventKind.SPEED_INCREASED)).toHaveLength(1);
  });
});

describe("CR 700 terms announced as events", () => {
  it("a permanent card put into a graveyard descends; a token or an instant doesn't (CR 700.11)", () => {
    const board = new Board();
    const game = board.game;
    const events = seen(board);
    const bears = createObject(game, CARDS["Grizzly Bears"], 0, Zone.GRAVEYARD);
    const token = createObject(game, CARDS["Grizzly Bears"], 0, Zone.BATTLEFIELD, { kind: ObjectKind.TOKEN });
    for (const obj of [bears, token]) emit(game, event(EventKind.ZONE_CHANGE, { objectId: obj.id, toZone: Zone.GRAVEYARD }));
    const descended = events.filter((e) => e.kind === EventKind.DESCENDED);
    expect(descended.map((e) => e.objectId)).toEqual([bears.id]);
  });

  it("casting a spell targeting an opponent is a crime and expends each total crossed (CR 700.13, 700.14)", () => {
    const board = new Board();
    const game = board.game;
    const events = seen(board);
    const spell = createObject(game, CARDS["Grizzly Bears"], 0, Zone.STACK);
    spell.manaSpent = 3;
    spell.targets = [[playerTarget(1)]];
    player(game, 0).manaSpentOnSpellsThisTurn = 2;
    emit(game, event(EventKind.CAST_SPELL, { objectId: spell.id, player: 0 }));
    expect(events.filter((e) => e.kind === EventKind.EXPENDED).map((e) => e.amount)).toEqual([3, 4, 5]);
    expect(events.filter((e) => e.kind === EventKind.CRIME_COMMITTED)).toHaveLength(1);
    spell.targets = [[playerTarget(0)]];
    emit(game, event(EventKind.CAST_SPELL, { objectId: spell.id, player: 0 }));
    expect(events.filter((e) => e.kind === EventKind.CRIME_COMMITTED)).toHaveLength(1);
  });
});
