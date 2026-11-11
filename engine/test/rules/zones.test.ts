// Ported from Game.move_object in mtgfish/rules/kernel/game.py and the
// zone-change cases of tests/rules (CR 400.7, 111.7, 304.4, 700.4, 122.1).
import { describe, expect, it } from "vitest";
import { CardType, Color, Zone } from "../../src/kernel/enums";
import { event, EventKind, type GameEvent } from "../../src/kernel/events";
import { createObject, player } from "../../src/kernel/game";
import { addCounters, counterCount, isLive, ObjectKind } from "../../src/kernel/gameobject";
import { installReplacementHooks } from "../../src/rules/cr600_spells_and_abilities/replacement_hooks";
import { moveObject } from "../../src/rules/zones";
import { Board, cardDef, CARDS, mana } from "./board";

const LIGHTNING_BOLT = cardDef("Lightning Bolt", { cost: mana(0, Color.RED), types: CardType.INSTANT, colors: Color.RED });
const JACE = { ...cardDef("Jace Beleren", { cost: mana(1, Color.BLUE, Color.BLUE), types: CardType.PLANESWALKER, colors: Color.BLUE }) };
JACE.faces = [{ ...(JACE.faces[0] as (typeof JACE.faces)[0]), loyalty: "3" }];

function watch(board: Board): GameEvent[] {
  const events: GameEvent[] = [];
  board.game.observer = (_g, e) => events.push(e);
  return events;
}

describe("moving objects (CR 400.7)", () => {
  it("makes a new object that remembers nothing, and keeps the husk for last-known information", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    addCounters(bears, "+1/+1", 2);
    bears.tapped = true;
    const dead = moveObject(board.game, bears, Zone.GRAVEYARD);
    expect(dead.id).not.toBe(bears.id);
    expect(dead.zone).toBe(Zone.GRAVEYARD);
    expect(counterCount(dead, "+1/+1")).toBe(0);
    expect(dead.tapped).toBe(false);
    expect(isLive(bears)).toBe(false);
    expect(bears.zone).toBe(Zone.BATTLEFIELD);
    expect(bears.lastKnownCharacteristics?.power).toBe(4);
    expect(player(board.game, 0).graveyard).toEqual([dead.id]);
    expect(board.game.battlefield).toEqual([]);
  });

  it("dying is battlefield to graveyard (CR 700.4), with leaves and put-into events", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const events = watch(board);
    moveObject(board.game, bears, Zone.GRAVEYARD);
    expect(events.map((e) => e.kind)).toEqual([
      EventKind.ZONE_CHANGE,
      EventKind.DESCENDED,
      EventKind.LEAVES_BATTLEFIELD,
      EventKind.DIES,
      EventKind.PUT_INTO_GRAVEYARD,
    ]);
  });

  it("a token that leaves the battlefield ceases to exist (CR 111.7)", () => {
    const board = new Board();
    const token = createObject(board.game, CARDS["Grizzly Bears"], 0, Zone.BATTLEFIELD, { kind: ObjectKind.TOKEN });
    const events = watch(board);
    const after = moveObject(board.game, token, Zone.GRAVEYARD);
    expect(after).toBe(token);
    expect(player(board.game, 0).graveyard).toEqual([]);
    expect(events.at(-1)?.kind).toBe(EventKind.CEASED_TO_EXIST);
    expect(events.some((e) => e.kind === EventKind.DIES)).toBe(true);
  });

  it("an instant put onto the battlefield stays where it is (CR 304.4)", () => {
    const board = new Board();
    const bolt = createObject(board.game, LIGHTNING_BOLT, 0, Zone.GRAVEYARD);
    expect(moveObject(board.game, bolt, Zone.BATTLEFIELD)).toBe(bolt);
    expect(bolt.zone).toBe(Zone.GRAVEYARD);
    expect(isLive(bolt)).toBe(true);
  });

  it("a permanent with a finality counter is exiled instead (CR 122.1)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    addCounters(bears, "finality", 1);
    expect(moveObject(board.game, bears, Zone.GRAVEYARD).zone).toBe(Zone.EXILE);
  });

  it("a flashback spell leaving the stack is exiled (CR 702.34a)", () => {
    const board = new Board();
    const spell = createObject(board.game, LIGHTNING_BOLT, 0, Zone.STACK);
    spell.alternativeCostPaid = "Flashback";
    expect(moveObject(board.game, spell, Zone.GRAVEYARD).zone).toBe(Zone.EXILE);
  });

  it("a planeswalker enters with its loyalty as counters, however it arrives (CR 306.5b)", () => {
    const board = new Board();
    const jace = createObject(board.game, JACE, 0, Zone.GRAVEYARD);
    const events = watch(board);
    const entered = moveObject(board.game, jace, Zone.BATTLEFIELD);
    expect(counterCount(entered, "loyalty")).toBe(3);
    expect(entered.summoningSick).toBe(true);
    expect(events.map((e) => e.kind)).toContain(EventKind.COUNTER_ADDED);
    expect(events.at(-1)?.kind).toBe(EventKind.ENTERS_BATTLEFIELD);
  });

  it("owned zones take the owner; the battlefield takes the given controller", () => {
    const board = new Board();
    const card = createObject(board.game, CARDS["Grizzly Bears"], 1, Zone.HAND);
    const stolen = moveObject(board.game, card, Zone.BATTLEFIELD, { toPlayer: 0 });
    expect(stolen.controller).toBe(0);
    expect(stolen.owner).toBe(1);
    const back = moveObject(board.game, stolen, Zone.HAND, { toPlayer: 0 });
    expect(player(board.game, 1).hand).toEqual([back.id]);
  });

  it("exile to exile is not a zone change (CR 406.7)", () => {
    const board = new Board();
    const card = createObject(board.game, CARDS["Grizzly Bears"], 0, Zone.EXILE);
    const events = watch(board);
    moveObject(board.game, card, Zone.EXILE);
    expect(events.some((e) => e.kind === EventKind.ZONE_CHANGE)).toBe(false);
  });

  it("a replacement can send it elsewhere, or prevent the move", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    installReplacementHooks({
      hasReplacements: () => true,
      replace: (_g, e) => (e.kind === EventKind.ZONE_CHANGE ? { ...e, toZone: Zone.EXILE } : e),
    });
    try {
      expect(moveObject(board.game, bears, Zone.GRAVEYARD).zone).toBe(Zone.EXILE);
      const other = board.play("Grizzly Bears");
      installReplacementHooks({ replace: () => null });
      expect(moveObject(board.game, other, Zone.GRAVEYARD)).toBe(other);
      expect(other.zone).toBe(Zone.BATTLEFIELD);
    } finally {
      installReplacementHooks({ hasReplacements: () => false, replace: (_g, e) => e });
    }
    void event;
  });
});
