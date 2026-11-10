// Ported from mtgfish/rules/kernel/events.py.
import { describe, expect, it } from "vitest";
import { EventKindValues } from "../ir";
import { Zone } from "./enums";
import { event, EventKind, eventText, isLeaveBattlefield, looksBackInTime, replaced, withAmount } from "./events";

describe("events", () => {
  it("event kinds are the IR's event kinds", () => {
    expect(EventKind).toEqual(EventKindValues);
  });

  it("knows which events look back in time (CR 603.10)", () => {
    expect(looksBackInTime(event(EventKind.DIES))).toBe(true);
    expect(looksBackInTime(event(EventKind.PHASED_OUT))).toBe(true);
    expect(looksBackInTime(event(EventKind.CONTROL_CHANGED))).toBe(true);
    expect(looksBackInTime(event(EventKind.ENTERS_BATTLEFIELD))).toBe(false);
    expect(looksBackInTime(event(EventKind.ZONE_CHANGE, { fromZone: Zone.GRAVEYARD }))).toBe(true);
    expect(looksBackInTime(event(EventKind.ZONE_CHANGE, { fromZone: Zone.HAND }))).toBe(false);
    expect(isLeaveBattlefield(event(EventKind.SACRIFICED))).toBe(true);
  });

  it("is edited by copying, never in place (CR 614.1)", () => {
    const damage = event(EventKind.DAMAGE_DEALT, { amount: 3, player: 1, source: 7 });
    const halved = withAmount(damage, 1);
    const moved = replaced(damage, { player: 2 });
    expect(damage.amount).toBe(3);
    expect(halved.amount).toBe(1);
    expect(moved.player).toBe(2);
    expect(eventText(damage)).toBe("DAMAGE_DEALT player=1 src=7 amount=3");
    expect(eventText(event(EventKind.ZONE_CHANGE, { objectId: 4, fromZone: Zone.BATTLEFIELD, toZone: Zone.GRAVEYARD })))
      .toBe("ZONE_CHANGE obj=4 BATTLEFIELD->GRAVEYARD");
  });
});
