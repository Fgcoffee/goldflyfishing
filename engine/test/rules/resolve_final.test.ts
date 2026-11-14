// Ported from tests/rules/test_resolve.py (modes, Class levels, cascade,
// discover, vote, piles of choices) and test_cr701_cascade.py.
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { classLevel } from "../../src/rules/cr300_card_types/card_types";
import { currentDungeon } from "../../src/rules/cr300_card_types/dungeons";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const n = (v: number) => ({ $: "Value" as const, constant: v });
const draw = (k: number) => E("DRAW", { amount: n(k) });

function stock(board: Board, names: string[]) {
  return names.map((name) => createObject(board.game, CARDS[name]!, 0, Zone.LIBRARY));
}

describe("the last executors", () => {
  it("a mana ability's modes ride on the resolution (CR 605.3b)", () => {
    const board = new Board();
    stock(board, ["Forest", "Forest", "Forest"]);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { chosenModes: [1] }), [E("CHOOSE_MODE", { children: [draw(1), draw(2)] })]);
    expect(player(board.game, 0).hand).toHaveLength(2);
  });

  it("a Class's level becomes N (CR 716.2a)", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    expect(classLevel(board.game, source.id)).toBe(1);
    execute(newResolution(board.game, source.id, 0), [E("SET_CLASS_LEVEL", { amount: n(2), targets: filter({ source_only: true }) })]);
    expect(classLevel(board.game, source.id)).toBe(2);
  });

  it("cascade exiles to a cheaper nonland card and casts it free; the rest go to the bottom (CR 702.85a)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    stock(board, ["Forest", "Serra Angel", "Grizzly Bears", "Island"]);
    const spell = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.STACK);
    execute(newResolution(board.game, spell.id, 0), [E("CASCADE")]);
    const stack = board.game.stack.map((id) => board.game.objects.get(id)!);
    expect(stack).toHaveLength(2);
    expect(player(board.game, 0).library).toHaveLength(3); // Forest and Angel went to the bottom; Island untouched
  });

  it("discover puts the card into the hand if it isn't cast (CR 701.57a)", () => {
    const board = new Board();
    stock(board, ["Forest", "Grizzly Bears"]);
    const source = board.play("Glorious Anthem");
    board.game.agents.set(0, { chooseOptional: () => false } as never);
    execute(newResolution(board.game, source.id, 0), [E("DISCOVER", { amount: n(3) })]);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(player(board.game, 0).library).toHaveLength(1);
  });

  it("a vote is tallied from the next player on, and the winner's child happens (CR 701.34)", () => {
    const board = new Board();
    stock(board, ["Forest", "Forest"]);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("VOTE", { children: [draw(1), draw(2)] })]);
    expect([...board.game.lastVote.entries()]).toEqual([[0, 2], [1, 0]]);
    expect(player(board.game, 0).hand).toHaveLength(1);
  });
});

describe("choosing a quality (CR 614.1b)", () => {
  it("with no agent, the creature type the chooser has most of; a planned colour wins", () => {
    const board = new Board();
    board.play("Grizzly Bears");
    board.play("Grizzly Bears");
    board.play("Serra Angel");
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("CHOOSE_QUALITY", { keywords: ["creature type"] })]);
    expect(source.chosenType).toBe("Bear");
    execute(newResolution(board.game, source.id, 0, { manaColor: 8 }), [E("CHOOSE_QUALITY", { keywords: ["color"] })]);
    expect(source.chosenColor).toBe(8);
  });
});

describe("VENTURE (CR 701.49)", () => {
  it("venturing puts a dungeon in the command zone, through dungeons.ts", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [
      { $: "Effect", kind: ir("EffectKind.VENTURE"), players: { $: "PlayerFilter", scope: ir("PlayerScope.YOU") } },
    ]);
    expect(currentDungeon(board.game, 0)).not.toBeNull();
    expect(currentDungeon(board.game, 1)).toBeNull();
  });
});
