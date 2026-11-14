// Ported from tests/rules/test_cr615_prevention.py and the resolution cases
// of test_cr614_replacement.py: shields and replacements a spell creates.
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { dealDamage } from "../../src/rules/cr100_game_concepts/actions";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { moveObject } from "../../src/rules/zones";
import { ALL_CREATURES, Board, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });

describe("prevention shields from a resolving spell (CR 615)", () => {
  it("'prevent the next 2 damage to target creature' protects that creature only, and is spent", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears");
    const other = board.play("Grizzly Bears");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[bears.id]] }), [
      E("PREVENT_DAMAGE", { targets: ALL_CREATURES, is_targeted: true, amount: { $: "Value", constant: 2 }, duration: ir("Duration.END_OF_TURN") }),
    ]);
    dealDamage(board.game, bears, 3, { source: source.id, sourceController: 0 });
    dealDamage(board.game, other, 1, { source: source.id, sourceController: 0 });
    dealDamage(board.game, bears, 1, { source: source.id, sourceController: 0 });
    expect([bears.damage, other.damage]).toEqual([2, 1]);
  });
});

describe("a replacement bound to the permanent (unearth, CR 702.84a)", () => {
  it("if it would leave the battlefield, it's exiled instead, once", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    execute(newResolution(board.game, bears.id, 0), [
      E("REPLACEMENT", { replacement_kind: 2, zone: ir("Zone.EXILE"), targets: filter({ source_only: true }) }),
    ]);
    const moved = moveObject(board.game, bears, Zone.GRAVEYARD);
    expect(moved.zone).toBe(Zone.EXILE);
  });
});
