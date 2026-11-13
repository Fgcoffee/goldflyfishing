// Ported from the prohibition/permission half of
// mtgfish/rules/cr500_turn_structure/restrictions.py (CR 101.2, 113.6).
import { describe, expect, it } from "vitest";
import type { Restriction } from "../../src/ir";
import { Act, permitted, prohibited, registerStanding, registerStandingPermission } from "../../src/rules/cr500_turn_structure/restrictions";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, effect, staticAbility } from "./board";

const restriction = (fields: Partial<Restriction>): Restriction => ({ $: "Restriction", act: Act.ATTACK as never, ...fields });
const players = (scope: string) => ({ $: "PlayerFilter" as const, scope: ir(`PlayerScope.${scope}`) });

describe("prohibitions (CR 101.2)", () => {
  it("a static 'creatures can't attack' forbids while its source is there", () => {
    const board = new Board();
    board.script("Ghostly Prison", staticAbility("Creatures can't attack.", effect({
      kind: ir("EffectKind.RESTRICTION"),
      targets: ALL_CREATURES,
      restrictions: [restriction({ act: Act.ATTACK as never })],
    })));
    const bears = board.play("Grizzly Bears", 1);
    const prison = board.play("Ghostly Prison");
    expect(prohibited(board.game, Act.ATTACK, { obj: bears })?.source).toBe(prison.id);
    expect(prohibited(board.game, Act.BLOCK, { obj: bears })).toBeNull();
    board.remove(prison);
    expect(prohibited(board.game, Act.ATTACK, { obj: bears })).toBeNull();
  });

  it("a restriction naming players is about those players", () => {
    const board = new Board();
    board.script("Levitation", staticAbility("Your opponents can't cast spells.", effect({
      kind: ir("EffectKind.RESTRICTION"),
      players: players("EACH_OPPONENT"),
      restrictions: [restriction({ act: Act.CAST_SPELL as never })],
    })));
    board.play("Levitation");
    expect(prohibited(board.game, Act.CAST_SPELL, { player: 1 })).not.toBeNull();
    expect(prohibited(board.game, Act.CAST_SPELL, { player: 0 })).toBeNull();
  });

  it("a standing restriction from a resolved spell applies, stamped with its turn", () => {
    const board = new Board();
    board.game.turn = 3;
    const entry = registerStanding(board.game, restriction({ act: Act.CAST_SPELL as never, players: players("EACH_PLAYER") }));
    expect(entry.created_turn).toBe(3);
    expect(prohibited(board.game, Act.CAST_SPELL, { player: 0 })).toBe(entry);
  });
});

describe("permissions (CR 113.6)", () => {
  it("a standing permission for you only", () => {
    const board = new Board();
    registerStandingPermission(board.game, restriction({
      act: Act.CAST_AS_THOUGH_FLASH as never, players: players("YOU"), controller: 0,
    }));
    expect(permitted(board.game, Act.CAST_AS_THOUGH_FLASH, { player: 0 })).not.toBeNull();
    expect(permitted(board.game, Act.CAST_AS_THOUGH_FLASH, { player: 1 })).toBeNull();
    expect(permitted(board.game, Act.PLAY_LAND, { player: 0 })).toBeNull();
  });
});
