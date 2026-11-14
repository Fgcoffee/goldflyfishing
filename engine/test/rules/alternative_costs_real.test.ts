// Ported from tests/rules/test_cr118_force_of_will.py (F56), on real cards
// (schema/fixtures): Force of Will's alternative cost - 1 life and a blue
// card exiled from hand - is offered with a spell to counter and another
// blue card, and counters it (CR 118.9, 601.2b). Python uses Brainstorm as
// the other blue card; Gitaxian Probe is the fixture standing in for it.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { perform, type Action } from "../../src/rules/cr100_game_concepts/priority";
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
function current(b: RealBoard, obj: GameObject): GameObject {
  let o = obj;
  while (o.supersededBy > 0) o = b.game.objects.get(o.supersededBy)!;
  return o;
}
function boltOnTheStack(b: RealBoard): GameObject {
  const bolt = b.hand("Lightning Bolt", 1);
  addMana(player(b.game, 1).manaPool, manaKind(Color.RED), 1);
  const cast = legalActions(b.game, 1).find((a) => a.source === bolt.id)!;
  expect(perform(b.game, 1, { ...cast, targets: [[playerTarget(0)]] })).toBe(true);
  return bolt;
}
const alternativeCasts = (b: RealBoard, fow: GameObject): Action[] =>
  legalActions(b.game, 0).filter((a) => a.source === fow.id && a.alternativeCost >= 0);

describe("Force of Will's alternative cost (CR 118.9, F56)", () => {
  it("is offered, takes 1 life and the other blue card, and counters the spell", () => {
    const b = board();
    const fow = b.hand("Force of Will");
    const probe = b.hand("Gitaxian Probe");
    const bolt = boltOnTheStack(b);
    const casts = alternativeCasts(b, fow);
    expect(casts).toHaveLength(1);
    const life = player(b.game, 0).life;
    expect(perform(b.game, 0, casts[0]!)).toBe(true);
    expect(player(b.game, 0).life).toBe(life - 1);
    expect(current(b, probe).zone).toBe(Zone.EXILE);
    while (b.game.stack.length > 0) resolveTop(b.game);
    expect(current(b, bolt).zone).toBe(Zone.GRAVEYARD);
    expect(player(b.game, 0).life).toBe(life - 1);
  });

  it("can't exile itself: no other blue card, no alternative cost", () => {
    const b = board();
    const fow = b.hand("Force of Will");
    boltOnTheStack(b);
    expect(alternativeCasts(b, fow)).toHaveLength(0);
  });
});
