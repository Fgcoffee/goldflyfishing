// Ported from tests/rules/test_casting.py, on real cards (schema/fixtures):
// timing, land drops, targets, resolution and paying. Cases already in
// casting.test.ts and legality.test.ts aren't repeated.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { addMana, manaKind, poolTotal } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, settle, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { castSpell } from "../../src/rules/cr600_spells_and_abilities/casting";
import { CastError } from "../../src/rules/cr600_spells_and_abilities/cast_error";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { checkStateBasedActions } from "../../src/rules/cr700_additional_rules/sba";
import { legalActions } from "../../src/rules/legality";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.priorityPlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
const giveMana = (b: RealBoard, p: number, n: number, color: Color = Color.RED): void =>
  addMana(player(b.game, p).manaPool, manaKind(color), n);
const casts = (b: RealBoard, card: GameObject, p = 0): Action[] =>
  legalActions(b.game, p).filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
const lands = (b: RealBoard, p = 0): Action[] => legalActions(b.game, p).filter((a) => a.kind === ActionKind.PLAY_LAND);
/** What an object has become, following CR 400.7's chain (hand -> stack -> battlefield). */
function current(b: RealBoard, obj: GameObject): GameObject {
  let o = obj;
  while (o.supersededBy > 0) o = b.game.objects.get(o.supersededBy)!;
  return o;
}
const castFirst = (b: RealBoard, card: GameObject, targets?: number[][]): void => {
  const action = casts(b, card)[0]!;
  expect(perform(b.game, 0, targets ? { ...action, targets } : action)).toBe(true);
};

describe("timing (CR 307.1, 117.1a)", () => {
  it("a sorcery-speed spell needs your main phase and an empty stack", () => {
    const b = board();
    giveMana(b, 0, 2, Color.GREEN);
    const bears = b.hand("Grizzly Bears");
    expect(casts(b, bears)).toHaveLength(1);
    b.game.step = Step.UPKEEP;
    b.game.phase = Phase.BEGINNING;
    expect(casts(b, bears)).toHaveLength(0);
  });

  it("not on an opponent's turn; an instant any time", () => {
    const b = board();
    b.game.activePlayer = 1;
    giveMana(b, 0, 2, Color.GREEN);
    giveMana(b, 0, 1, Color.RED);
    expect(casts(b, b.hand("Grizzly Bears"))).toHaveLength(0);
    b.play("Grizzly Bears", 1);
    expect(casts(b, b.hand("Lightning Bolt")).length).toBeGreaterThan(0);
  });
});

describe("land drops (CR 305.2)", () => {
  it("one land per turn, not offered once used; a land taps for mana the turn it arrives", () => {
    const b = board();
    b.hand("Mountain");
    b.hand("Forest");
    expect(lands(b)).toHaveLength(2);
    expect(perform(b.game, 0, lands(b)[0]!)).toBe(true);
    expect(lands(b)).toHaveLength(0);
    const mana = legalActions(b.game, 0).filter((a) => a.kind === ActionKind.ACTIVATE_MANA_ABILITY);
    expect(mana.length).toBeGreaterThan(0);
  });
});

describe("targets (CR 601.2c, 608.2b)", () => {
  it("a spell needing a target isn't castable without one, and is once one exists", () => {
    const b = board();
    giveMana(b, 0, 1, Color.BLACK);
    giveMana(b, 0, 1, Color.RED);
    const terminate = b.hand("Terminate");
    expect(casts(b, terminate)).toHaveLength(0);
    b.play("Grizzly Bears", 1);
    expect(casts(b, terminate).length).toBeGreaterThan(0);
  });

  it("a spell resolves onto its target", () => {
    const b = board();
    giveMana(b, 0, 1);
    const bears = b.play("Grizzly Bears", 1);
    castFirst(b, b.hand("Lightning Bolt"), [[bears.id]]);
    resolveTop(b.game);
    checkStateBasedActions(b.game);
    expect(b.game.battlefield.includes(bears.id)).toBe(false);
  });

  it("a spell whose only target is gone is countered, and goes to the graveyard (CR 608.2b)", () => {
    const b = board();
    giveMana(b, 0, 1);
    const bears = b.play("Grizzly Bears", 1);
    const bolt = b.hand("Lightning Bolt");
    castFirst(b, bolt, [[bears.id]]);
    const lifeBefore = player(b.game, 1).life;
    b.game.battlefield.splice(b.game.battlefield.indexOf(bears.id), 1);
    bears.zone = Zone.GRAVEYARD;
    bears.supersededBy = -1;
    resolveTop(b.game);
    expect(player(b.game, 1).life).toBe(lifeBefore);
    expect(player(b.game, 0).graveyard).toHaveLength(1);
  });
});

describe("resolving and paying (CR 601.2g-h, 608.3)", () => {
  it("a permanent spell becomes a permanent; an instant goes to the graveyard", () => {
    const b = board();
    giveMana(b, 0, 2, Color.GREEN);
    const bears = b.hand("Grizzly Bears");
    castFirst(b, bears);
    resolveTop(b.game);
    expect(current(b, bears).zone).toBe(Zone.BATTLEFIELD);

    giveMana(b, 0, 1);
    castFirst(b, b.hand("Lightning Bolt"), [[playerTarget(1)]]);
    resolveTop(b.game);
    expect(player(b.game, 0).graveyard).toHaveLength(1);
  });

  it("casting spends mana from the pool, or taps lands when it is empty", () => {
    const b = board();
    giveMana(b, 0, 2, Color.GREEN);
    castFirst(b, b.hand("Grizzly Bears"));
    expect(poolTotal(player(b.game, 0).manaPool)).toBe(0);
    resolveTop(b.game);

    const forests = [b.play("Forest"), b.play("Forest")];
    castFirst(b, b.hand("Grizzly Bears"));
    expect(forests.every((f) => f.tapped)).toBe(true);
  });
});

// More of test_casting.py, now that the parser exported Ambush Viper,
// Gladecover Scout, Shock and Wrath of God; and test_cr603_cast_this_spell.py.
describe("flash, hexproof, untargeted spells, cast triggers", () => {
  it("flash makes a creature instant speed (CR 702.8a)", () => {
    const b = board();
    b.game.activePlayer = 1;
    giveMana(b, 0, 2, Color.GREEN);
    expect(casts(b, b.hand("Ambush Viper")).length).toBeGreaterThan(0);
  });

  it("hexproof stops an opponent's targeting, not its controller's (CR 702.11b)", () => {
    const b = board();
    giveMana(b, 0, 2);
    const theirs = b.play("Gladecover Scout", 1);
    const mine = b.play("Gladecover Scout", 0);
    const shock = b.hand("Shock");
    expect(casts(b, shock).length).toBeGreaterThan(0);
    const handBefore = player(b.game, 0).hand.length;
    expect(() => castSpell(b.game, 0, { ...casts(b, shock)[0]!, targets: [[theirs.id]] })).toThrow(CastError);
    expect(player(b.game, 0).hand.length).toBe(handBefore);
    expect(b.game.stack).toHaveLength(0);
    // The refused cast is rewound (CR 732); a second Shock casts at your own.
    const second = b.hand("Shock");
    expect(castSpell(b.game, 0, { ...casts(b, second)[0]!, targets: [[mine.id]] }).zone).toBe(Zone.STACK);
  });

  it("an untargeted spell hits whatever is there as it resolves (CR 608.2)", () => {
    const b = board();
    giveMana(b, 0, 2, Color.WHITE);
    giveMana(b, 0, 2);
    const early = b.play("Grizzly Bears", 1);
    castFirst(b, b.hand("Wrath of God"));
    const late = b.play("Grizzly Bears", 1);
    resolveTop(b.game);
    expect(b.game.battlefield.includes(early.id)).toBe(false);
    expect(b.game.battlefield.includes(late.id)).toBe(false);
  });

  it("casting Wretched Gryff triggers its 'when you cast this spell' (CR 113.6k)", () => {
    const b = board();
    // Python puts three Islands in the library to draw from.
    const library = player(b.game, 0).library;
    for (let i = 0; i < 3; i += 1) {
      const card = b.hand("Mountain");
      player(b.game, 0).hand.splice(player(b.game, 0).hand.indexOf(card.id), 1);
      card.zone = Zone.LIBRARY;
      library.push(card.id);
    }
    giveMana(b, 0, 8, Color.BLUE);
    const gryff = b.hand("Wretched Gryff");
    const handBefore = player(b.game, 0).hand.length;
    const action = casts(b, gryff).find((a) => a.alternativeCost < 0)!;
    expect(perform(b.game, 0, action)).toBe(true);
    settle(b.game);
    for (let i = 0; i < 5 && b.game.stack.length > 0; i += 1) resolveTop(b.game);
    // The Gryff left the hand and the trigger drew one.
    expect(player(b.game, 0).hand.length).toBe(handBefore);
  });
});
