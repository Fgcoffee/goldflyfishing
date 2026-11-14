// Ported from tests/rules/test_cr613_granted_timestamps.py: a granted
// ability takes the granting effect's timestamp; a printed one the object's
// (CR 613.7a). On the real matcher.
import { beforeEach, describe, expect, it } from "vitest";
import type { Ability, Effect, ObjectFilter } from "../../src/ir";
import { Duration } from "../../src/kernel/enums";
import type { GameObject } from "../../src/kernel/gameobject";
import { NO_OBJECT } from "../../src/kernel/ids";
import { newResolution, registerContinuous } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { installMatch } from "../../src/rules/match";
import { Board } from "./board";

const CREATURES: ObjectFilter = { $: "ObjectFilter", types_all: ir("CardType.CREATURE") };
const setPt = (p: number, t: number, text: string, targets?: ObjectFilter): Effect => ({
  $: "Effect",
  kind: ir("EffectKind.SET_PT"),
  ...(targets ? { targets, duration: Duration.PERMANENT as never } : {}),
  amount: { $: "Value", constant: p },
  amount2: { $: "Value", constant: t },
  text,
});
const staticOf = (effect: Effect, text: string): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), effects: [effect], text });
const base5: Ability = staticOf(setPt(5, 5, "base 5/5"), "has base 5/5");

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
});

/** A spell resolving and creating a continuous effect (CR 611.2). */
function resolve(effect: Effect): void {
  registerContinuous(newResolution(board.game, NO_OBJECT, 0), effect);
  board.refresh();
}
const everyone2x2 = (): void => resolve(setPt(2, 2, "creatures have base power and toughness 2/2", CREATURES));
const grant = (obj: GameObject, ability: Ability): void =>
  resolve({
    $: "Effect",
    kind: ir("EffectKind.GRANT_ABILITY"),
    targets: { $: "ObjectFilter", specific: [obj.id] },
    granted_abilities: [ability],
    duration: Duration.PERMANENT as never,
  });

describe("timestamps of granted abilities (CR 613.7a)", () => {
  it("a granted ability beats an effect created before the grant", () => {
    const bear = board.play("Grizzly Bears");
    everyone2x2();
    expect(board.pt(bear)).toEqual([2, 2]);
    grant(bear, base5);
    expect(board.pt(bear)).toEqual([5, 5]);
  });

  it("a grant made first still loses to a later effect", () => {
    const bear = board.play("Grizzly Bears");
    grant(bear, base5);
    expect(board.pt(bear)).toEqual([5, 5]);
    everyone2x2();
    expect(board.pt(bear)).toEqual([2, 2]);
  });

  it("a printed ability keeps the object's timestamp", () => {
    board.script("Grizzly Bears", base5);
    const bear = board.play("Grizzly Bears");
    expect(board.pt(bear)).toEqual([5, 5]);
    everyone2x2();
    expect(board.pt(bear)).toEqual([2, 2]);
  });
});
