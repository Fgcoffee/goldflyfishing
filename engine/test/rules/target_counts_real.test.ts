// Ported from tests/rules/test_cr601_target_counts.py, on real cards
// (schema/fixtures): "two target creatures" takes exactly two (CR 601.2c,
// 115.1d). Python uses Hill Giant; Wretched Gryff stands in. Python's last
// case is Ulamog, the Ceaseless Hunger (its cast trigger exiles two); the
// fixture is Ulamog, the Infinite Gyre (asked for by mistake), whose cast
// trigger destroys one target permanent - adapted to that.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, settle, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
const offers = (b: RealBoard, card: GameObject): Action[] =>
  legalActions(b.game, 0).filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
const live = (game: Game, name: string): GameObject[] =>
  [...game.objects.values()].filter((o) => !o.supersededBy && (o.card as { name?: string } | null)?.name === name);
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 10 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}

describe("two targets (CR 601.2c, 115.1d)", () => {
  it("Wicked Pact destroys both targets by default", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    b.play("Wretched Gryff", 1);
    const pact = b.hand("Wicked Pact");
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 3);
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 2);
    const actions = offers(b, pact);
    expect(actions).toHaveLength(1);
    expect(perform(b.game, 0, actions[0]!)).toBe(true);
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
    expect(live(b.game, "Wretched Gryff").map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
  });

  it("two targets are needed to cast it", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    const pact = b.hand("Wicked Pact");
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 5);
    expect(offers(b, pact)).toHaveLength(0);
  });

  it("naming one target for two is refused", () => {
    const b = board();
    const bears = b.play("Grizzly Bears", 1);
    b.play("Wretched Gryff", 1);
    const pact = b.hand("Wicked Pact");
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 5);
    expect(perform(b.game, 0, { ...offers(b, pact)[0]!, targets: [[bears.id]] })).toBe(false);
  });

  it("Ulamog, the Infinite Gyre's cast trigger destroys its target", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    const ulamog = b.hand("Ulamog, the Infinite Gyre");
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 11);
    const actions = offers(b, ulamog);
    expect(actions.length).toBeGreaterThan(0);
    expect(perform(b.game, 0, actions[0]!)).toBe(true);
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
    expect(live(b.game, "Ulamog, the Infinite Gyre").map((o) => o.zone)).toEqual([Zone.BATTLEFIELD]);
  });
});
