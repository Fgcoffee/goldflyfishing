// mana-spent-trigger (rules/dummies.ts), on real cards: mana an ability made
// carries the dummy's rider until it is spent; spent on a spell that fits,
// the rider happens (CR 106.12). Path of Ancestry (scry 1), Pyromancer's
// Goggles (copy the spell), Boseiju, Who Shelters All (can't be countered).
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, invalidateAllCharacteristics, player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, PassingAgent, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { hasKeyword } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { IMPLEMENTED_DUMMIES, spentRiderOf } from "../../src/rules/dummies";
import { legalActions } from "../../src/rules/legality";
import { realCard, RealBoard } from "./real_cards";

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
function cast(b: RealBoard, p: number, card: GameObject): GameObject {
  const act = legalActions(b.game, p).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
  expect(act).toBeDefined();
  expect(perform(b.game, p, act!)).toBe(true);
  settle(b.game);
  return now(b.game, card);
}
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 20 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
/** Player 0's commander, in the command zone. */
function commander(b: RealBoard, card: string): void {
  const obj = createObject(b.game, realCard(card), 0, Zone.COMMAND);
  player(b.game, 0).commanders.push(obj.id);
}

describe("mana-spent-trigger", () => {
  it("Path of Ancestry: its mana spent on a creature sharing a type with the commander: scry 1", () => {
    const b = board();
    commander(b, "Grizzly Bears"); // a Bear, green
    const path = b.play("Path of Ancestry", 0);
    b.play("Forest", 0);
    createObject(b.game, realCard("Forest"), 0, Zone.LIBRARY);
    const bears = b.hand("Grizzly Bears", 0);
    const spell = cast(b, 0, bears); // {1}{G}: Path and the Forest
    expect(path.tapped).toBe(true);
    expect(spell.zone).toBe(Zone.STACK);
    expect(b.game.stack).toHaveLength(2);
    const top = b.game.objects.get(b.game.stack[b.game.stack.length - 1]!)!;
    expect(top.source).toBe(path.id);
    // It resolves: the library's top card is looked at (scry 1), and the Bears resolve after.
    resolveAll(b);
    expect(now(b.game, bears).zone).toBe(Zone.BATTLEFIELD);
  });

  it("not on a creature that shares no type with the commander", () => {
    const b = board();
    commander(b, "Kenrith, the Returned King"); // Human Noble
    const path = b.play("Path of Ancestry", 0);
    b.play("Forest", 0);
    const spell = cast(b, 0, b.hand("Grizzly Bears", 0));
    expect(path.tapped).toBe(true);
    expect(b.game.stack).toEqual([spell.id]);
  });

  it("Pyromancer's Goggles: its {R} spent on Lightning Bolt copies it", () => {
    const b = board();
    b.play("Pyromancer's Goggles", 0);
    const bolt = b.hand("Lightning Bolt", 0);
    cast(b, 0, bolt);
    expect(b.game.stack).toHaveLength(2); // the Bolt and the copy trigger
    resolveAll(b);
    // Two Bolts (the spell and its copy), whoever the test's agent aimed them at.
    expect(b.game.players.reduce((t, p) => t + p.life, 0)).toBe(80 - 6);
  });

  it("Boseiju, Who Shelters All: its {C} spent on a sorcery makes it uncounterable", () => {
    const b = board();
    const boseiju = b.play("Boseiju, Who Shelters All", 0);
    boseiju.tapped = false;
    const pool = player(b.game, 0).manaPool;
    addMana(pool, manaKind(Color.BLACK), 2);
    addMana(pool, manaKind(Color.NONE), 1); // {2}{B}{B}: Boseiju pays the rest
    const spell = cast(b, 0, b.hand("Stronghold Discipline", 0));
    expect(boseiju.tapped).toBe(true);
    expect(player(b.game, 0).life).toBe(38); // its 2 life
    expect(hasKeyword(characteristicsOf(b.game, spell), "Can't be countered")).toBe(true);
  });

  it("but not on a creature spell", () => {
    const b = board();
    const boseiju = b.play("Boseiju, Who Shelters All", 0);
    boseiju.tapped = false;
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1);
    const spell = cast(b, 0, b.hand("Grizzly Bears", 0));
    expect(boseiju.tapped).toBe(true);
    expect(hasKeyword(characteristicsOf(b.game, spell), "Can't be countered")).toBe(false);
  });

  it("Hall of the Bandit Lord: its {C} spent on a creature spell: the creature has haste, and keeps it", () => {
    const b = board();
    const hall = b.play("Hall of the Bandit Lord", 0);
    hall.tapped = false;
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1);
    const bears = b.hand("Grizzly Bears", 0);
    cast(b, 0, bears); // {1}{G}: the Hall's {C} pays the {1}
    expect(hall.tapped).toBe(true);
    resolveAll(b);
    const permanent = now(b.game, bears);
    expect(permanent.zone).toBe(Zone.BATTLEFIELD);
    expect(hasKeyword(characteristicsOf(b.game, permanent), "Haste")).toBe(true);
    b.game.turn += 1; // "it gains haste": no end
    invalidateAllCharacteristics(b.game);
    expect(hasKeyword(characteristicsOf(b.game, permanent), "Haste")).toBe(true);
  });

  it("Carnelian Orb of Dragonkind: only a Dragon creature spell gains haste, until end of turn", () => {
    const b = board();
    b.play("Carnelian Orb of Dragonkind", 0);
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1);
    const bears = b.hand("Grizzly Bears", 0); // not a Dragon
    cast(b, 0, bears);
    resolveAll(b);
    expect(hasKeyword(characteristicsOf(b.game, now(b.game, bears)), "Haste")).toBe(false);
    expect(spentRiderOf("If that mana is spent on a Dragon creature spell, it gains haste until end of turn.")).toMatchObject({
      quality: "dragon",
      does: "haste",
      untilEndOfTurn: true,
    });
  });

  it("is listed as implemented; other wordings stay unread", () => {
    expect(IMPLEMENTED_DUMMIES.has("mana-spent-trigger")).toBe(true);
    expect(spentRiderOf("If that mana is spent on a creature spell, it gains riot.")).toBeNull();
  });
});
