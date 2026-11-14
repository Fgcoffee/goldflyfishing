// Ported from tests/rules/test_cr608_pay_if_you_do.py and the Rest in Peace
// case of test_cr608_untargeted_count.py, on real cards (schema/fixtures):
// "you may pay {1}. If you do, ..." happens only when paid (CR 608.2d), and
// Rest in Peace's "exile all cards from all graveyards" reaches every one.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind, poolTotal } from "../../src/rules/cr100_game_concepts/mana_pool";
import { PassingAgent, settle } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { moveObject } from "../../src/rules/zones";
import { realCard, RealBoard } from "./real_cards";

function board(saysYes = true): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, Object.assign(new PassingAgent(), { chooseOptional: () => saysYes && p.id === 0 }));
  return b;
}
/** Onto the battlefield from hand, with its "enters" event (RealBoard.play is silent). */
const enter = (b: RealBoard, name: string, owner = 0): GameObject =>
  moveObject(b.game, createObject(b.game, realCard(name), owner, Zone.HAND), Zone.BATTLEFIELD);
const live = (game: Game, name: string): GameObject[] =>
  [...game.objects.values()].filter((o) => !o.supersededBy && (o.card as { name?: string } | null)?.name === name);
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 10 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
const colourless = (b: RealBoard, n: number): void => addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), n);

describe("'you may pay. If you do' (CR 608.2d)", () => {
  it.each([0, 1])("Mentor of the Meek draws only when paid (%i mana)", (mana) => {
    const b = board();
    for (let i = 0; i < 5; i += 1) createObject(b.game, realCard("Island"), 0, Zone.LIBRARY);
    enter(b, "Mentor of the Meek");
    resolveAll(b);
    if (mana) colourless(b, mana);
    const hand = player(b.game, 0).hand.length;
    enter(b, "Grizzly Bears");
    resolveAll(b);
    expect(player(b.game, 0).hand.length).toBe(hand + mana);
  });

  it.each([0, 4])("Nim Deathmantle returns the creature only when paid (%i mana)", (mana) => {
    const b = board();
    b.play("Nim Deathmantle");
    const bears = b.play("Grizzly Bears");
    if (mana) colourless(b, mana);
    destroy(b.game, bears);
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([mana ? Zone.BATTLEFIELD : Zone.GRAVEYARD]);
  });

  it("a declined payment does nothing", () => {
    const b = board(false);
    b.play("Nim Deathmantle");
    const bears = b.play("Grizzly Bears");
    colourless(b, 4);
    destroy(b.game, bears);
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
    expect(poolTotal(player(b.game, 0).manaPool)).toBe(4);
  });
});

describe("Rest in Peace (CR 608.2c)", () => {
  it("exiles every card from every graveyard as it enters", () => {
    const b = board();
    createObject(b.game, realCard("Grizzly Bears"), 0, Zone.GRAVEYARD);
    createObject(b.game, realCard("Shock"), 1, Zone.GRAVEYARD);
    enter(b, "Rest in Peace");
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.EXILE]);
    expect(live(b.game, "Shock").map((o) => o.zone)).toEqual([Zone.EXILE]);
    expect(player(b.game, 0).graveyard).toHaveLength(0);
    expect(player(b.game, 1).graveyard).toHaveLength(0);
  });
});
