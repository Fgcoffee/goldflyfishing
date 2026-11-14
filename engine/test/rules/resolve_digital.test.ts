// Ported from tests/rules/test_digital_mechanics.py (the executor half):
// seek, and perpetually following a card from zone to zone.
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { moveObject } from "../../src/rules/zones";
import { Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });

describe("MTG Arena mechanics", () => {
  it("seek puts a matching card from the library into the hand, and 'it' is that card", () => {
    const board = new Board();
    createObject(board.game, CARDS["Forest"]!, 0, Zone.LIBRARY);
    const bears = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.LIBRARY);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("SEEK", { targets: filter({ types_all: ir("CardType.CREATURE"), zones: { $set: [Zone.LIBRARY] } }) })]);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(board.game.objects.get(r.remembered[0]!)!.previousId).toBe(bears.id);
  });

  it("a perpetual +1/+1 on a card in hand is still there on the battlefield", () => {
    const board = new Board();
    const card = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[card.id]] }), [
      E("PERPETUALLY", {
        targets: filter({ zones: { $set: [Zone.HAND] } }), is_targeted: true,
        children: [E("MODIFY_PT", { amount: { $: "Value", constant: 1 }, amount2: { $: "Value", constant: 1 } })],
      }),
    ]);
    expect(characteristicsOf(board.game, card).power).toBe(3);
    const permanent = moveObject(board.game, card, Zone.BATTLEFIELD);
    expect(characteristicsOf(board.game, permanent).power).toBe(3);
  });
});
