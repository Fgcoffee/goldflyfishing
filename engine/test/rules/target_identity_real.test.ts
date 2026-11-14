// Ported from tests/rules/test_cr115_target_identity.py, on real cards
// (schema/fixtures): "another target" is a criterion the choice must meet
// (CR 115.3, 601.2c), and an illegal first target leaves "its power"
// undeterminable (CR 608.2b). Python uses Hill Giant; Wretched Gryff (3/4)
// is the fixture standing in. The parser-reading cases stay in Python.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { bounce } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, PassingAgent, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { chooseTargets, targetedNodes } from "../../src/rules/cr600_spells_and_abilities/targeting";
import { checkStateBasedActions } from "../../src/rules/cr700_additional_rules/sba";
import { legalActions } from "../../src/rules/legality";
import { list } from "../../src/rules/ir_read";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
const mana = (b: RealBoard, n: number, color: Color = Color.NONE): void => addMana(player(b.game, 0).manaPool, manaKind(color), n);
const castOf = (b: RealBoard, card: GameObject): Action | undefined =>
  legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
const alive = (obj: GameObject): boolean => obj.zone === Zone.BATTLEFIELD && !obj.supersededBy;
const consumeMana = (b: RealBoard): void => {
  mana(b, 1);
  mana(b, 1, Color.GREEN);
  mana(b, 1, Color.BLACK);
};

describe("'another target' (CR 115.3, 601.2c)", () => {
  it("Consume Strength pumps one creature and shrinks another", () => {
    const b = board();
    const bears = b.play("Grizzly Bears", 0);
    const gryff = b.play("Wretched Gryff", 1);
    consumeMana(b);
    const spell = b.hand("Consume Strength");
    expect(perform(b.game, 0, { ...castOf(b, spell)!, targets: [[bears.id], [gryff.id]] })).toBe(true);
    resolveTop(b.game);
    expect([b.chars(bears).power, b.chars(bears).toughness]).toEqual([4, 4]);
    expect([b.chars(gryff).power, b.chars(gryff).toughness]).toEqual([1, 2]);
  });

  it("the same creature can't be both targets", () => {
    const b = board();
    const bears = b.play("Grizzly Bears", 0);
    b.play("Wretched Gryff", 1);
    consumeMana(b);
    const spell = b.hand("Consume Strength");
    expect(perform(b.game, 0, { ...castOf(b, spell)!, targets: [[bears.id], [bears.id]] })).toBe(false);
    expect(b.game.stack).toHaveLength(0);
  });

  it("one creature isn't enough for two distinct targets", () => {
    const b = board();
    b.play("Grizzly Bears", 0);
    consumeMana(b);
    const spell = b.hand("Consume Strength");
    expect(castOf(b, spell)).toBeUndefined();
    b.play("Grizzly Bears", 1);
    expect(castOf(b, spell)).toBeDefined();
  });

  it("a chooser that picks one creature twice is corrected", () => {
    const b = board();
    const bears = b.play("Grizzly Bears", 0);
    const gryff = b.play("Wretched Gryff", 1);
    const spell = b.hand("Consume Strength");
    b.game.agents.set(
      0,
      Object.assign(new PassingAgent(), {
        chooseTargets: (_g: unknown, _p: number, _s: number, candidates: readonly (readonly number[])[]) => candidates.map(() => [bears.id]),
      }),
    );
    const ability = b.chars(spell).abilities.find((a) => a.trigger == null && list(a.effects).length > 0)!;
    const nodes = targetedNodes(list(ability.effects));
    expect(nodes).toHaveLength(2);
    expect(chooseTargets(b.game, spell, nodes, 0)).toEqual([[bears.id], [gryff.id]]);
  });
});

describe("Deadshot: 'tap target creature; it deals damage equal to its power to another'", () => {
  function deadshot(b: RealBoard, shooter: GameObject, victim: GameObject): void {
    mana(b, 3);
    mana(b, 1, Color.RED);
    const spell = b.hand("Deadshot");
    expect(perform(b.game, 0, { ...castOf(b, spell)!, targets: [[shooter.id], [victim.id]] })).toBe(true);
  }

  it("taps one creature and it deals its power to the other", () => {
    const b = board();
    const gryff = b.play("Wretched Gryff", 0);
    const bears = b.play("Grizzly Bears", 1);
    deadshot(b, gryff, bears);
    resolveTop(b.game);
    checkStateBasedActions(b.game);
    expect(gryff.tapped).toBe(true);
    expect(alive(bears)).toBe(false);
    expect(alive(gryff)).toBe(true);
  });

  it("deals nothing when its first target is gone (CR 608.2b)", () => {
    const b = board();
    const gryff = b.play("Wretched Gryff", 0);
    const bears = b.play("Grizzly Bears", 1);
    deadshot(b, gryff, bears);
    bounce(b.game, gryff);
    resolveTop(b.game);
    expect(alive(bears)).toBe(true);
    expect(bears.damage).toBe(0);
  });
});
