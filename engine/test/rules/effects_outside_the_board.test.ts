// Ported from tests/rules/test_cr613_effects_outside_the_board.py: a static
// ability that speaks of another zone reaches it (CR 611.3, 613). The
// Airbend cases are left out: Python builds that grant with
// cr701_keyword_actions.build, which the TS engine has no counterpart of
// (the parser expands keywords at export).
import { beforeEach, describe, expect, it } from "vitest";
import type { Ability, ObjectFilter } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { createObject } from "../../src/kernel/game";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import { ir } from "../../src/rules/ir_read";
import { installMatch } from "../../src/rules/match";
import { Board, CARDS } from "./board";

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
});

const grantFlying = (targets: ObjectFilter): Ability => ({
  $: "Ability",
  kind: ir("AbilityKind.STATIC"),
  text: "creature cards in your graveyard have flying",
  effects: [{ $: "Effect", kind: ir("EffectKind.GRANT_ABILITY"), targets, keywords: ["Flying"] }],
});
const inYourGraveyard: ObjectFilter = {
  $: "ObjectFilter",
  types_all: ir("CardType.CREATURE"),
  owner: ir("ControllerRelation.YOU"),
  zones: { $set: [Zone.GRAVEYARD] },
};
const graveyard = (owner: number) => createObject(board.game, CARDS["Grizzly Bears"]!, owner, Zone.GRAVEYARD);

describe("a static ability that speaks of another zone", () => {
  it("reaches creature cards in your graveyard, not an opponent's", () => {
    board.script("Glorious Anthem", grantFlying(inYourGraveyard));
    board.play("Glorious Anthem");
    const mine = graveyard(0);
    const theirs = graveyard(1);
    board.refresh();
    expect(board.keywords(mine).has("Flying")).toBe(true);
    expect(board.keywords(theirs).has("Flying")).toBe(false);
  });

  it("stops when the static ability leaves (CR 611.3b)", () => {
    board.script("Glorious Anthem", grantFlying(inYourGraveyard));
    const anthem = board.play("Glorious Anthem");
    const mine = graveyard(0);
    destroy(board.game, anthem);
    board.refresh();
    expect(board.keywords(mine).has("Flying")).toBe(false);
  });

  it("a battlefield filter doesn't reach the graveyard", () => {
    board.script("Glorious Anthem", grantFlying({ $: "ObjectFilter", types_all: ir("CardType.CREATURE"), owner: ir("ControllerRelation.YOU") }));
    board.play("Glorious Anthem");
    const mine = graveyard(0);
    board.refresh();
    expect(board.keywords(mine).has("Flying")).toBe(false);
  });
});
