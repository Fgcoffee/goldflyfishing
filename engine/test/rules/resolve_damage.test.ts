// Ported from tests/rules/test_cr120_damage.py, test_cr119_life.py and the
// damage/fight cases of test_resolve.py.
import { describe, expect, it } from "vitest";
import type { Ability, Effect } from "../../src/ir";
import { player } from "../../src/kernel/game";
import { playerTarget } from "../../src/kernel/ids";
import { dealDamage } from "../../src/rules/cr100_game_concepts/actions";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const keyword = (word: string): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: word, text: word });
const n = (v: number) => ({ $: "Value" as const, constant: v });
const anyTarget = filter({ ...ALL_CREATURES, includes_players: true });

describe("damage (CR 120)", () => {
  it("is marked on a creature, and a player loses that much life", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears", 1);
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0, { targets: [[bears.id], [playerTarget(1)]] });
    execute(r, [E("DAMAGE", { targets: anyTarget, is_targeted: true, amount: n(1) }), E("DAMAGE", { targets: anyTarget, is_targeted: true, amount: n(3) })]);
    expect(bears.damage).toBe(1);
    expect(player(board.game, 1).life).toBe(37);
    expect(r.chosenPlayers).toEqual([1]);
  });

  it("deathtouch is recorded, lifelink gains its controller that much", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    board.script("Serra Angel", keyword("Deathtouch"), keyword("Lifelink"));
    const angel = board.play("Serra Angel");
    const bears = board.play("Grizzly Bears", 1);
    dealDamage(board.game, bears, 1, { source: angel.id, sourceController: 0, deathtouch: true, lifelink: true });
    expect(bears.dealtDeathtouchDamage).toBe(true);
    expect(player(board.game, 0).life).toBe(41);
  });

  it("infect gives a player poison instead of life loss (CR 702.90c)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    board.script("Grizzly Bears", keyword("Infect"));
    const bears = board.play("Grizzly Bears");
    dealDamage(board.game, 1, 2, { source: bears.id, sourceController: 0 });
    expect(player(board.game, 1).poison).toBe(2);
    expect(player(board.game, 1).life).toBe(40);
  });

  it("zero damage is no damage (CR 120.8)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears");
    expect(dealDamage(board.game, bears, 0)).toBe(0);
    expect(dealDamage(board.game, bears, -2)).toBe(0);
  });
});

describe("fight and life", () => {
  it("two creatures fight: each deals damage equal to its power to the other (CR 701.14a)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears");
    const angel = board.play("Serra Angel", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[bears.id, angel.id]] }), [
      E("FIGHT", { targets: filter({ ...ALL_CREATURES, count: n(2) }), is_targeted: true }),
    ]);
    expect([bears.damage, angel.damage]).toEqual([4, 2]);
  });

  it("set life gains or loses the difference", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("SET_LIFE", { amount: n(10) })]);
    expect(player(board.game, 0).life).toBe(10);
    expect(player(board.game, 0).lifeLostThisTurn).toBe(30);
  });
});
