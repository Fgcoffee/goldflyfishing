// Ported from tests/rules/test_cr601_additional_cost_record.py, on real cards
// (schema/fixtures): a sacrifice is a cost paid while casting (CR 601.2h),
// and what it consumed is read as it last existed (CR 608.2h). Python uses
// Bone Splinters, Colossal Dreadmaw, Hill Giant and Wall of Stone; Fling,
// Wretched Gryff (3/4) and Ulamog (10/10) stand in. The parser-reading
// cases (Eldritch Evolution, Toxic Deluge) stay in Python.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { addCounters, type GameObject } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, settle, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { realCard, RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const color of [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN]) addMana(player(b.game, 0).manaPool, manaKind(color), 4);
  for (let i = 0; i < 10; i += 1) createObject(b.game, realCard("Forest"), 0, Zone.LIBRARY);
  return b;
}
const offer = (b: RealBoard, card: GameObject): Action | undefined =>
  legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
function cast(b: RealBoard, name: string, targets: number[][] = [], resolve = true): void {
  const card = b.hand(name);
  const action = offer(b, card);
  expect(action, `${name} can't be cast`).toBeDefined();
  expect(perform(b.game, 0, { ...action!, targets })).toBe(true);
  if (!resolve) return;
  settle(b.game);
  for (let i = 0; i < 10 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}

describe("a sacrifice paid as a cost (CR 601.2h, 608.2h)", () => {
  it("is paid while casting: the creature is in the graveyard with the spell still on the stack", () => {
    const b = board();
    const bears = b.play("Grizzly Bears");
    cast(b, "Fling", [[playerTarget(1)]], false);
    expect(b.game.stack.length).toBeGreaterThan(0);
    expect(b.game.objects.get(bears.supersededBy)?.zone).toBe(Zone.GRAVEYARD);
  });

  it("no creature, no cast", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    expect(offer(b, b.hand("Fling"))).toBeUndefined();
  });

  it("Fling deals the sacrificed creature's power as it last existed, counters included", () => {
    const b = board();
    const bears = b.play("Grizzly Bears");
    addCounters(bears, "+1/+1", 3);
    b.game.epoch += 1;
    const before = player(b.game, 1).life;
    cast(b, "Fling", [[playerTarget(1)]]);
    expect(player(b.game, 1).life).toBe(before - 5);
  });

  it("Life's Legacy draws the sacrificed creature's power", () => {
    const b = board();
    b.play("Wretched Gryff");
    const hand = player(b.game, 0).hand.length;
    cast(b, "Life's Legacy");
    expect(player(b.game, 0).hand.length).toBe(hand + 3);
  });

  it("Momentous Fall reads power, then toughness, off the same creature", () => {
    const b = board();
    b.play("Wretched Gryff");
    const me = player(b.game, 0);
    const [hand, life] = [me.hand.length, me.life];
    cast(b, "Momentous Fall");
    expect(me.hand.length).toBe(hand + 3);
    expect(me.life).toBe(life + 4);
  });

  it("Worthy Cause gains the sacrificed creature's toughness", () => {
    const b = board();
    b.play("Wretched Gryff");
    const life = player(b.game, 0).life;
    cast(b, "Worthy Cause");
    expect(player(b.game, 0).life).toBe(life + 4);
  });

  it("Call for Blood shrinks its target by the sacrificed power, fixed as it resolves", () => {
    const b = board();
    b.play("Wretched Gryff");
    const target = b.play("Ulamog, the Infinite Gyre", 1);
    cast(b, "Call for Blood", [[target.id]]);
    b.game.epoch += 1;
    expect([b.chars(target).power, b.chars(target).toughness]).toEqual([7, 7]);
  });
});
