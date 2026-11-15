// Triage of the rules-owned findings in audit/AUDIT_LOG.md on real cards
// (schema/fixtures), on the TS engine. Each describe names its finding. A
// finding fixed (in Python before the port, or here) is a plain test; one
// still open is `it.fails`, which turns red the day it is fixed.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { EventKind, event } from "../../src/kernel/events";
import { createObject, player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { playerTarget } from "../../src/kernel/ids";
import { dealDamage, destroy } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, PassingAgent, perform, settle, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { emit } from "../../src/rules/emit";
import { legalActions } from "../../src/rules/legality";
import { moveObject } from "../../src/rules/zones";
import { DEFAULT_TURN_OPTIONS, turnBasedActions } from "../../src/rules/cr500_turn_structure/turn";
import { realCard, RealBoard } from "./real_cards";

function board(yes: (player: number) => boolean = () => true): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, Object.assign(new PassingAgent(), { chooseOptional: () => yes(p.id) }));
  return b;
}
const name = (o: GameObject): string => (o.card as { name?: string } | null)?.name ?? "";
const live = (game: Game, card: string): GameObject[] => [...game.objects.values()].filter((o) => !o.supersededBy && name(o) === card);
const zones = (game: Game, card: string): Zone[] => live(game, card).map((o) => o.zone);
/** Onto the battlefield from hand, with its "enters" event (RealBoard.play is silent). */
const enter = (b: RealBoard, card: string, owner = 0): GameObject =>
  moveObject(b.game, createObject(b.game, realCard(card), owner, Zone.HAND), Zone.BATTLEFIELD);
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 20 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
const pool = (b: RealBoard, p: number, color: Color, n: number): void => addMana(player(b.game, p).manaPool, manaKind(color), n);
const casts = (b: RealBoard, p: number, card: GameObject): Action[] =>
  legalActions(b.game, p).filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);
function upkeep(b: RealBoard, p = 0): void {
  b.game.turn += 1;
  b.game.activePlayer = p;
  b.game.phase = Phase.BEGINNING;
  b.game.step = Step.UNTAP;
  turnBasedActions(b.game, Step.UNTAP, DEFAULT_TURN_OPTIONS);
  b.game.step = Step.UPKEEP;
  emit(b.game, event(EventKind.STEP_BEGAN, { player: p, amount: Step.UPKEEP }));
  turnBasedActions(b.game, Step.UPKEEP, DEFAULT_TURN_OPTIONS);
  resolveAll(b);
}

describe("F28: overload (CR 702.96a)", () => {
  it("Cyclonic Rift overloaded returns every nonland permanent an opponent controls", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    b.play("Hill Giant", 1);
    b.play("Sol Ring", 1);
    const rift = b.hand("Cyclonic Rift");
    pool(b, 0, Color.BLUE, 1);
    pool(b, 0, Color.NONE, 6);
    const overload = casts(b, 0, rift).filter((a) => a.alternativeCost >= 0);
    expect(overload.length).toBeGreaterThan(0);
    expect(perform(b.game, 0, overload[0]!)).toBe(true);
    resolveAll(b);
    expect([...zones(b.game, "Grizzly Bears"), ...zones(b.game, "Hill Giant"), ...zones(b.game, "Sol Ring")]).toEqual([Zone.HAND, Zone.HAND, Zone.HAND]);
  });
});

describe("F28 controls", () => {
  it("overloaded, it leaves my permanents and the opponent's lands alone", () => {
    const b = board();
    b.play("Grizzly Bears", 1);
    b.play("Forest", 1);
    b.play("Hill Giant", 0);
    const rift = b.hand("Cyclonic Rift");
    pool(b, 0, Color.BLUE, 1);
    pool(b, 0, Color.NONE, 6);
    expect(perform(b.game, 0, casts(b, 0, rift).find((a) => a.alternativeCost >= 0)!)).toBe(true);
    resolveAll(b);
    expect([...zones(b.game, "Grizzly Bears"), ...zones(b.game, "Forest"), ...zones(b.game, "Hill Giant")]).toEqual([Zone.HAND, Zone.BATTLEFIELD, Zone.BATTLEFIELD]);
  });
  it("cast normally, it returns only its target", () => {
    const b = board();
    const bears = b.play("Grizzly Bears", 1);
    b.play("Hill Giant", 1);
    const rift = b.hand("Cyclonic Rift");
    pool(b, 0, Color.BLUE, 2);
    const normal = casts(b, 0, rift).find((a) => a.alternativeCost < 0)!;
    expect(perform(b.game, 0, { ...normal, targets: [[bears.id]] })).toBe(true);
    resolveAll(b);
    expect([...zones(b.game, "Grizzly Bears"), ...zones(b.game, "Hill Giant")]).toEqual([Zone.HAND, Zone.BATTLEFIELD]);
  });
});

describe("F54: echo and cumulative upkeep are paid", () => {
  it("Mogg War Marshal, echo accepted with no mana: sacrificed", () => {
    const b = board();
    enter(b, "Mogg War Marshal");
    resolveAll(b);
    upkeep(b);
    expect(zones(b.game, "Mogg War Marshal")).toEqual([Zone.GRAVEYARD]);
  });
  it("Mogg War Marshal, echo accepted with {1}{R}: paid and kept", () => {
    const b = board();
    enter(b, "Mogg War Marshal");
    resolveAll(b);
    pool(b, 0, Color.RED, 2);
    upkeep(b);
    expect(zones(b.game, "Mogg War Marshal")).toEqual([Zone.BATTLEFIELD]);
    expect(player(b.game, 0).manaPool.buckets.size).toBe(0);
  });
  it("Mogg War Marshal, echo accepted with two Mountains: they are tapped to pay it", () => {
    const b = board();
    const lands = [b.play("Mountain"), b.play("Mountain")];
    enter(b, "Mogg War Marshal");
    resolveAll(b);
    upkeep(b);
    expect(zones(b.game, "Mogg War Marshal")).toEqual([Zone.BATTLEFIELD]);
    expect(lands.map((l) => l.tapped)).toEqual([true, true]);
  });
  it("Mystic Remora, cumulative upkeep accepted with no mana: sacrificed", () => {
    const b = board();
    b.play("Mystic Remora");
    upkeep(b);
    expect(zones(b.game, "Mystic Remora")).toEqual([Zone.GRAVEYARD]);
  });
});

describe("F60: an Enchant player Curse (CR 303.4, 704.5m)", () => {
  it("Curse of the Pierced Heart never sits on the battlefield attached to nothing", () => {
    const b = board();
    const curse = b.hand("Curse of the Pierced Heart");
    pool(b, 0, Color.RED, 2);
    const offers = casts(b, 0, curse);
    if (offers.length > 0) {
      perform(b.game, 0, offers[0]!);
      resolveAll(b);
    }
    for (const c of live(b.game, "Curse of the Pierced Heart")) {
      if (c.zone === Zone.BATTLEFIELD) expect(c.attachedPlayer).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("karoo lands: 'sacrifice it unless you return' (fixed in TS)", () => {
  it("Coral Atoll returns an untapped Island and stays", () => {
    const b = board();
    b.play("Island");
    enter(b, "Coral Atoll");
    resolveAll(b);
    expect(zones(b.game, "Coral Atoll")).toEqual([Zone.BATTLEFIELD]);
    expect(zones(b.game, "Island")).toEqual([Zone.HAND]);
  });
  it("Coral Atoll with no untapped Island is sacrificed", () => {
    const b = board();
    b.play("Island").tapped = true;
    enter(b, "Coral Atoll");
    resolveAll(b);
    expect(zones(b.game, "Coral Atoll")).toEqual([Zone.GRAVEYARD]);
    expect(zones(b.game, "Island")).toEqual([Zone.BATTLEFIELD]);
  });

  it("Simic Growth Chamber returns a land and stays (the other land, given one)", () => {
    const b = board();
    b.play("Forest");
    enter(b, "Simic Growth Chamber");
    resolveAll(b);
    expect(zones(b.game, "Simic Growth Chamber")).toEqual([Zone.BATTLEFIELD]);
    expect(zones(b.game, "Forest")).toEqual([Zone.HAND]);
  });
});

describe("F27: protection from a colour (CR 702.16)", () => {
  it("Lightning Bolt can target White Knight (pro-black)", () => {
    const b = board();
    const knight = b.play("White Knight", 1);
    const bolt = b.hand("Lightning Bolt");
    pool(b, 0, Color.RED, 1);
    expect(casts(b, 0, bolt).some((a) => a.targets.flat().includes(knight.id)) || perform(b.game, 0, { ...casts(b, 0, bolt)[0]!, targets: [[knight.id]] })).toBe(true);
  });
});

describe("F30: simultaneous deaths and look-back triggers (CR 603.10a)", () => {
  it("Damnation with Blood Artist and 4 Bears: 5 triggers", () => {
    const b = board();
    b.play("Blood Artist");
    for (let i = 0; i < 4; i += 1) b.play("Grizzly Bears");
    const damnation = b.hand("Damnation");
    pool(b, 0, Color.BLACK, 4);
    expect(perform(b.game, 0, casts(b, 0, damnation)[0]!)).toBe(true);
    let lost = 0;
    b.game.observer = (_g, e) => void (e.kind === EventKind.LIFE_LOST && (lost += 1));
    resolveAll(b);
    expect(lost).toBe(5);
  });
});

describe("F32: can't be regenerated (CR 701.19c)", () => {
  it("Wrath of God destroys Drudge Skeletons with a regeneration shield", () => {
    const b = board();
    const skel = b.play("Drudge Skeletons", 1);
    skel.regenerationShields = 1;
    const wrath = b.hand("Wrath of God");
    pool(b, 0, Color.WHITE, 4);
    expect(perform(b.game, 0, casts(b, 0, wrath)[0]!)).toBe(true);
    resolveAll(b);
    expect(zones(b.game, "Drudge Skeletons")).toEqual([Zone.GRAVEYARD]);
  });
});

describe("F35: Doubling Season and entering loyalty (CR 306.5b, 614.1c)", () => {
  it("Liliana Vess enters with 10 loyalty", () => {
    const b = board();
    b.play("Doubling Season");
    const lili = b.hand("Liliana Vess");
    pool(b, 0, Color.BLACK, 5);
    expect(perform(b.game, 0, casts(b, 0, lili)[0]!)).toBe(true);
    resolveAll(b);
    expect(live(b.game, "Liliana Vess")[0]!.counters.get("loyalty")).toBe(10);
  });
});

describe("F41/F52: cast triggers (CR 601.2i, 603.4)", () => {
  it("Wretched Gryff's cast trigger draws", () => {
    const b = board();
    for (let i = 0; i < 3; i += 1) createObject(b.game, realCard("Island"), 0, Zone.LIBRARY);
    const gryff = b.hand("Wretched Gryff");
    pool(b, 0, Color.BLUE, 1);
    pool(b, 0, Color.NONE, 7);
    const normal = casts(b, 0, gryff).find((a) => a.alternativeCost < 0)!;
    expect(perform(b.game, 0, normal)).toBe(true);
    resolveAll(b);
    expect(player(b.game, 0).hand).toHaveLength(1);
  });
  it("unkicked Sowing Mycospawn puts one cast trigger on the stack, not the kicked one", () => {
    const b = board();
    const myco = b.hand("Sowing Mycospawn");
    pool(b, 0, Color.GREEN, 1);
    pool(b, 0, Color.NONE, 3);
    const plain = casts(b, 0, myco).find((a) => a.additionalCosts.length === 0)!;
    expect(perform(b.game, 0, plain)).toBe(true);
    settle(b.game);
    expect(b.game.stack.length).toBe(2);
  });
});

describe("F42: goad ends (CR 701.15a)", () => {
  it("Disrupt Decorum's goad is gone at the caster's next turn", () => {
    const b = board();
    const giant = b.play("Hill Giant", 1);
    const dd = b.hand("Disrupt Decorum");
    pool(b, 0, Color.RED, 2);
    pool(b, 0, Color.NONE, 2);
    expect(perform(b.game, 0, casts(b, 0, dd)[0]!)).toBe(true);
    resolveAll(b);
    expect(giant.goadedBy.size).toBeGreaterThan(0);
    upkeep(b, 1);
    expect(giant.goadedBy.size).toBeGreaterThan(0);
    b.game.turn += 1;
    b.game.activePlayer = 0;
    turnBasedActions(b.game, Step.UNTAP, DEFAULT_TURN_OPTIONS);
    expect(giant.goadedBy.size).toBe(0);
  });
});

describe("F53/F53b: player protection and hexproof", () => {
  it("after Teferi's Protection, the opponent's Bolt can't target me", () => {
    const b = board();
    const tp = b.hand("Teferi's Protection");
    pool(b, 0, Color.WHITE, 1);
    pool(b, 0, Color.NONE, 2);
    expect(perform(b.game, 0, casts(b, 0, tp)[0]!)).toBe(true);
    resolveAll(b);
    const bolt = b.hand("Lightning Bolt", 1);
    pool(b, 1, Color.RED, 1);
    expect(casts(b, 1, bolt).some((a) => a.targets.flat().includes(playerTarget(0)))).toBe(false);
  });
  it("True Believer: no one can target me", () => {
    const b = board();
    b.play("True Believer");
    const bolt = b.hand("Lightning Bolt", 1);
    pool(b, 1, Color.RED, 1);
    expect(casts(b, 1, bolt).length === 0 || !perform(b.game, 1, { ...casts(b, 1, bolt)[0]!, targets: [[playerTarget(0)]] })).toBe(true);
  });
});

describe("F55: Sly Instigator goads its target", () => {
  it("the targeted creature is goaded", () => {
    const b = board();
    const sly = b.play("Sly Instigator");
    const giant = b.play("Hill Giant", 1);
    pool(b, 0, Color.BLUE, 1);
    pool(b, 0, Color.NONE, 3);
    const act = legalActions(b.game, 0).find((a) => a.kind === ActionKind.ACTIVATE_ABILITY && a.source === sly.id);
    expect(act).toBeDefined();
    expect(perform(b.game, 0, { ...act!, targets: [[giant.id]] })).toBe(true);
    resolveAll(b);
    expect(giant.goadedBy.size).toBeGreaterThan(0);
  });
});

describe("F57: bestow needs a creature (CR 702.103b)", () => {
  it("Boon Satyr's bestow is not offered with no creature", () => {
    const b = board();
    const satyr = b.hand("Boon Satyr");
    pool(b, 0, Color.GREEN, 5);
    expect(casts(b, 0, satyr).filter((a) => a.alternativeCost >= 0)).toHaveLength(0);
  });
});

describe("F22: granted undying/persist return once (CR 702.93a, 702.79a)", () => {
  it("Rattleblaze Scarecrow (persist with a black creature) + Vampire Nighthawk: back once, then stays dead", () => {
    const b = board();
    const crow = b.play("Rattleblaze Scarecrow");
    b.play("Vampire Nighthawk");
    destroy(b.game, crow);
    resolveAll(b);
    expect(zones(b.game, "Rattleblaze Scarecrow")).toEqual([Zone.BATTLEFIELD]);
    destroy(b.game, live(b.game, "Rattleblaze Scarecrow")[0]!);
    resolveAll(b);
    expect(zones(b.game, "Rattleblaze Scarecrow")).toEqual([Zone.GRAVEYARD]);
  });
  it("Rattleblaze Scarecrow without a black creature stays dead", () => {
    const b = board();
    destroy(b.game, b.play("Rattleblaze Scarecrow"));
    resolveAll(b);
    expect(zones(b.game, "Rattleblaze Scarecrow")).toEqual([Zone.GRAVEYARD]);
  });
});

describe("F8: modular moves its counters by last-known information (CR 702.43a)", () => {
  it("Arcbound Worker's counter goes to Ornithopter", () => {
    const b = board();
    const worker = enter(b, "Arcbound Worker");
    resolveAll(b);
    const thopter = b.play("Ornithopter");
    destroy(b.game, worker);
    resolveAll(b);
    expect(thopter.counters.get("+1/+1") ?? 0).toBe(1);
  });
});
