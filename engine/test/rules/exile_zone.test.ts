// Ported from tests/rules/test_cr406_exile.py: CR 406.7, exiling what is
// already exiled makes a new object without a zone change.
import { describe, expect, it } from "vitest";
import { Zone } from "../../src/kernel/enums";
import { EventKind } from "../../src/kernel/events";
import { zoneList } from "../../src/kernel/game";
import { exile } from "../../src/rules/cr100_game_concepts/actions";
import { Board } from "./board";

function watch(board: Board): EventKind[] {
  const seen: EventKind[] = [];
  board.game.observer = (_g, e) => void seen.push(e.kind);
  return seen;
}

describe("the exile zone (CR 406)", () => {
  it("exiling from the battlefield changes zones (CR 400.7)", () => {
    const board = new Board();
    const obj = board.play("Grizzly Bears");
    const seen = watch(board);
    exile(board.game, obj);
    expect(seen).toContain(EventKind.ZONE_CHANGE);
    expect(seen).toContain(EventKind.LEAVES_BATTLEFIELD);
  });

  it("re-exiling makes a new object, is still exiled, but is no zone change (CR 406.7)", () => {
    const board = new Board();
    const first = exile(board.game, board.play("Grizzly Bears"));
    const seen = watch(board);
    const second = exile(board.game, first);
    expect(second.id).not.toBe(first.id);
    expect(seen).not.toContain(EventKind.ZONE_CHANGE);
    expect(seen).toContain(EventKind.EXILED);
  });

  it("only the new object is in exile, and the old one is superseded", () => {
    const board = new Board();
    const first = exile(board.game, board.play("Grizzly Bears"));
    const second = exile(board.game, first);
    expect(zoneList(board.game, Zone.EXILE, 0)).toEqual([second.id]);
    expect(first.supersededBy).toBe(second.id);
    expect(second.previousId).toBe(first.id);
  });
});
