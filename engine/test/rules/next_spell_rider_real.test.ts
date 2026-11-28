// next-spell-rider (rules/dummies.ts), on real cards: "the next spell you
// cast this turn can't be countered / has improvise", from the parser's
// DUMMY effect's words. The rider waits for the controller's next spell this
// turn that fits, and that spell has it while it is a spell.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, PassingAgent, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { hasKeyword } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { IMPLEMENTED_DUMMIES, spellRiderOf, waitingRiders } from "../../src/rules/dummies";
import { legalActions } from "../../src/rules/legality";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, new PassingAgent());
  return b;
}
function now(game: Game, o: GameObject): GameObject {
  while (o.supersededBy && game.objects.has(o.supersededBy)) o = game.objects.get(o.supersededBy)!;
  return o;
}
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 20 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
function activate(b: RealBoard, land: GameObject): void {
  const acts = legalActions(b.game, 0).filter(
    (a) => a.kind === ActionKind.ACTIVATE_ABILITY && a.source === land.id,
  );
  const rider = acts.find((a) => {
    const ab = (characteristicsOf(b.game, land).abilities as unknown as { effects?: { rule?: string }[] }[])[a.abilityIndex as number];
    return ab?.effects?.some((e) => e.rule === "next-spell-rider");
  });
  expect(rider).toBeDefined();
  expect(perform(b.game, 0, rider!)).toBe(true);
  resolveAll(b);
}
const cast = (b: RealBoard, p: number, card: GameObject) => {
  const act = legalActions(b.game, p).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
  expect(act).toBeDefined();
  expect(perform(b.game, p, act!)).toBe(true);
  return now(b.game, card);
};

describe("next-spell-rider", () => {
  it("Mistrise Village: the next spell can't be countered; Counterspell does nothing to it", () => {
    const b = board();
    const village = b.play("Mistrise Village", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLUE), 1);
    activate(b, village);
    expect(waitingRiders(b.game, 0, b.hand("Grizzly Bears", 0))).toHaveLength(1);
    const bears = b.hand("Grizzly Bears", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 2);
    const spell = cast(b, 0, bears);
    expect(spell.zone).toBe(Zone.STACK);
    expect(hasKeyword(characteristicsOf(b.game, spell), "Can't be countered")).toBe(true);
    // Only the next spell: the rider is gone.
    expect(b.game.spellRiders).toHaveLength(0);
    const counter = b.hand("Counterspell", 1);
    addMana(player(b.game, 1).manaPool, manaKind(Color.BLUE), 2);
    cast(b, 1, counter);
    resolveAll(b);
    expect(now(b.game, bears).zone).toBe(Zone.BATTLEFIELD);
  });

  it("without the rider, Counterspell counters it", () => {
    const b = board();
    const bears = b.hand("Grizzly Bears", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 2);
    cast(b, 0, bears);
    addMana(player(b.game, 1).manaPool, manaKind(Color.BLUE), 2);
    cast(b, 1, b.hand("Counterspell", 1));
    resolveAll(b);
    expect(now(b.game, bears).zone).toBe(Zone.GRAVEYARD);
  });

  it("Archway of Innovation: the next spell has improvise, so artifacts pay its generic part", () => {
    const b = board();
    const archway = b.play("Archway of Innovation", 0);
    const thopters = [b.play("Ornithopter", 0), b.play("Ornithopter", 0)];
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLUE), 1);
    activate(b, archway);
    const bears = b.hand("Grizzly Bears", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1); // {1}{G}: the {1} from an Ornithopter
    const spell = cast(b, 0, bears);
    expect(spell.zone).toBe(Zone.STACK);
    expect(thopters.filter((t) => t.tapped)).toHaveLength(1);
  });

  it("a rider not used this turn is gone the next", () => {
    const b = board();
    const village = b.play("Mistrise Village", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLUE), 1);
    activate(b, village);
    b.game.turn += 1;
    expect(waitingRiders(b.game, 0, b.hand("Grizzly Bears", 0))).toHaveLength(0);
  });

  it("reads the other wordings: which spells, and the rider", () => {
    const read = (text: string) => spellRiderOf({ $: "Effect", kind: 998, rule: "next-spell-rider", text } as never);
    expect(read("The next instant or sorcery spell you cast this turn can't be countered.")).toEqual({ spells: "instant or sorcery", rider: "can't be countered" });
    expect(read("The next noncreature spell you cast this turn has cascade.")).toEqual({ spells: "noncreature", rider: "cascade" });
    expect(read("The next spell you cast this turn has convoke.")).toEqual({ spells: "", rider: "convoke" });
    expect(read("The next spell you cast this turn costs {1} less.")).toBeNull();
    expect(IMPLEMENTED_DUMMIES.has("next-spell-rider")).toBe(true);
  });
});
