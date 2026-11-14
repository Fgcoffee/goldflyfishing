// Ported from tests/rules/test_cr118_alternative_costs.py (the commander
// cases; Force of Will is alternative_costs_real.test.ts) and
// test_cr601_granted_cast_abilities.py, on real cards (schema/fixtures).
// Python uses Fierce Guardianship; Deflecting Swat ("if you control a
// commander, you may cast this spell without paying its mana cost") stands
// in. Python scripts a conspire grant onto Serra Angel for red spells and
// taps Goblin Pikers; Wort, the Raidmother grants it to red or green spells,
// so Giant Growth with two green creatures stands in.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { addMana, manaKind, poolTotal } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { castSpell } from "../../src/rules/cr600_spells_and_abilities/casting";
import { legalActions } from "../../src/rules/legality";
import { realCard, RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
function opponentCastsBolt(b: RealBoard): void {
  const bolt = b.hand("Lightning Bolt", 1);
  addMana(player(b.game, 1).manaPool, manaKind(Color.RED), 1);
  const cast = legalActions(b.game, 1).find((a) => a.source === bolt.id)!;
  expect(perform(b.game, 1, { ...cast, targets: [[playerTarget(0)]] })).toBe(true);
}
const alternativeCasts = (b: RealBoard, card: GameObject): Action[] =>
  legalActions(b.game, 0).filter((a) => a.source === card.id && a.alternativeCost >= 0);
function makeCommander(b: RealBoard, obj: GameObject, controller?: number): void {
  obj.isCommander = true;
  if (controller !== undefined) obj.controller = obj.baseController = controller;
  b.game.epoch += 1;
}

describe("free if you control a commander (CR 903.3d)", () => {
  it("needs a commander permanent: a creature isn't one; then it's free", () => {
    const b = board();
    const swat = b.hand("Deflecting Swat");
    opponentCastsBolt(b);
    expect(alternativeCasts(b, swat)).toHaveLength(0);
    b.play("Grizzly Bears");
    expect(alternativeCasts(b, swat)).toHaveLength(0);
    makeCommander(b, b.play("Kenrith, the Returned King"));
    const actions = alternativeCasts(b, swat);
    expect(actions.length).toBeGreaterThan(0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.RED), 3);
    const before = poolTotal(player(b.game, 0).manaPool);
    expect(perform(b.game, 0, actions[0]!)).toBe(true);
    expect(poolTotal(player(b.game, 0).manaPool)).toBe(before);
  });

  it("a commander in the command zone isn't controlled (CR 108.4)", () => {
    const b = board();
    const swat = b.hand("Deflecting Swat");
    opponentCastsBolt(b);
    makeCommander(b, createObject(b.game, realCard("Kenrith, the Returned King"), 0, Zone.COMMAND));
    expect(alternativeCasts(b, swat)).toHaveLength(0);
  });

  it("any commander you control counts, an opponent's they control doesn't", () => {
    const b = board();
    const swat = b.hand("Deflecting Swat");
    opponentCastsBolt(b);
    const kenrith = b.play("Kenrith, the Returned King", 1);
    makeCommander(b, kenrith);
    expect(alternativeCasts(b, swat)).toHaveLength(0);
    makeCommander(b, kenrith, 0);
    expect(alternativeCasts(b, swat).length).toBeGreaterThan(0);
  });

  it("no target, no free cast (CR 601.2c)", () => {
    const b = board();
    const swat = b.hand("Deflecting Swat");
    makeCommander(b, b.play("Kenrith, the Returned King"));
    expect(alternativeCasts(b, swat)).toHaveLength(0);
  });
});

describe("a granted conspire (CR 702.78a, 601.2b)", () => {
  function withWort(grant: boolean): { b: RealBoard; growth: GameObject } {
    const b = board();
    if (grant) b.play("Wort, the Raidmother");
    else b.play("Grizzly Bears");
    b.play("Grizzly Bears");
    b.play("Llanowar Elves");
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1);
    return { b, growth: b.hand("Giant Growth") };
  }
  const additional = (b: RealBoard, card: GameObject): string[] =>
    legalActions(b.game, 0)
      .filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id)
      .map((a) => JSON.stringify(a.additionalCosts))
      .filter((v, i, all) => all.indexOf(v) === i)
      .sort();

  it("is offered, and paying it taps two creatures sharing the spell's colour", () => {
    const { b, growth } = withWort(true);
    expect(additional(b, growth)).toEqual(["[0]", "[]"]);
    const action = legalActions(b.game, 0).find((a) => a.source === growth.id && a.additionalCosts.length > 0)!;
    castSpell(b.game, 0, action);
    const green = [...b.game.objects.values()].filter((o) => !o.supersededBy && o.zone === Zone.BATTLEFIELD && (b.chars(o).colors & Color.GREEN) !== 0);
    expect(green.filter((o) => o.tapped)).toHaveLength(2);
  });

  it("without the grant, no conspire", () => {
    const { b, growth } = withWort(false);
    expect(additional(b, growth)).toEqual(["[]"]);
  });
});
