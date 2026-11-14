// Ported from tests/rules/test_combat_removal.py (CR 506.4) and
// test_cr510_damage_division.py (CR 510.1c-d, 702.19b). Python calls the
// private _attacker_assignment; here the combat record is set up directly
// and the damage actually dealt is checked.
import { describe, expect, it } from "vitest";
import type { Ability } from "../../src/ir";
import { CardType, Phase } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { NO_OBJECT } from "../../src/kernel/ids";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import {
  checkRemovalFromCombat,
  dealCombatDamage,
  declareAttackers,
  declareBlockers,
  newCombat,
  type Combat,
  type Defender,
} from "../../src/rules/cr500_turn_structure/combat";
import { ir } from "../../src/rules/ir_read";
import { moveObject } from "../../src/rules/zones";
import { Zone } from "../../src/kernel/enums";
import { setAgent } from "./agent_util";
import { Board, CARDS, cardDef } from "./board";

const kw = (keyword: string): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword, text: keyword });
const define = (name: string, types: number, power?: number, toughness?: number): string => {
  CARDS[name] = cardDef(name, { types, ...(power !== undefined ? { power: String(power), toughness: String(toughness) } : {}) });
  return name;
};
const BEARS = define("Bear", CardType.CREATURE, 2, 2);
const RUNECLAW = define("Runeclaw", CardType.CREATURE, 2, 2);
const ANGEL = define("Big Angel", CardType.CREATURE, 4, 4);
const RHOX = define("Big Rhox", CardType.CREATURE, 5, 4);
const ROCK = define("Rock", CardType.ARTIFACT);
const WALKER = define("Walker", CardType.PLANESWALKER);

function board(scripts: Record<string, string[]> = {}): Board {
  const b = new Board();
  for (const [name, keywords] of Object.entries(scripts)) b.script(name, ...keywords.map(kw));
  b.game.activePlayer = 0;
  b.game.phase = Phase.COMBAT;
  return b;
}

const combat = (b: Board): Combat => b.game.combat as Combat;

function attack(b: Board, attacks: [GameObject, Defender][]): void {
  setAgent(b.game, 0, { declareAttackers: () => new Map(attacks.map(([o, d]) => [o.id, d])) });
  declareAttackers(b.game);
}

function block(b: Board, blocks: [GameObject, GameObject][] = []): void {
  setAgent(b.game, 1, { declareBlockers: () => new Map(blocks.map(([blocker, attacker]) => [blocker.id, [attacker.id]])) });
  declareBlockers(b.game);
}

/** Make it stop being what it was: its card becomes a noncreature (stands in for a type-changing effect). */
function becomeArtifact(b: Board, obj: GameObject): void {
  obj.card = CARDS[ROCK];
  b.generation += 1; // the printed characteristics are cached by provider generation
  b.refresh();
}

describe("removal from combat (CR 506.4)", () => {
  it("an attacker or blocker that leaves the battlefield is removed", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    const blocker = b.play(BEARS, 1);
    attack(b, [[bears, 1]]);
    block(b, [[blocker, bears]]);
    moveObject(b.game, blocker, Zone.GRAVEYARD);
    checkRemovalFromCombat(b.game);
    expect(combat(b).blocking.has(blocker.id)).toBe(false);
    expect(combat(b).wasBlocked.has(bears.id)).toBe(true); // CR 509.1h
    moveObject(b.game, bears, Zone.HAND);
    checkRemovalFromCombat(b.game);
    expect(combat(b).attacking.has(bears.id)).toBe(false);
  });

  it("a stolen attacker deals no combat damage", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, 1]]);
    block(b);
    bears.controller = 1;
    dealCombatDamage(b.game);
    expect(player(b.game, 1).life).toBe(40);
  });

  it("a blocker whose controller changes is removed", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    const blocker = b.play(BEARS, 1);
    attack(b, [[bears, 1]]);
    block(b, [[blocker, bears]]);
    blocker.controller = 0;
    expect(checkRemovalFromCombat(b.game)).toEqual([blocker.id]);
  });

  it("a phased-out attacker is removed and deals no damage (CR 702.26b)", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, 1]]);
    block(b);
    bears.phasedOut = true;
    dealCombatDamage(b.game);
    expect(combat(b).attacking.has(bears.id)).toBe(false);
    expect(player(b.game, 1).life).toBe(40);
  });

  it("an attacker or blocker that stops being a creature is removed", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    const blocker = b.play(BEARS, 1);
    const other = b.play(RUNECLAW, 0);
    attack(b, [[bears, 1], [other, 1]]);
    block(b, [[blocker, other]]);
    becomeArtifact(b, bears);
    becomeArtifact(b, blocker);
    const removed = checkRemovalFromCombat(b.game);
    expect(removed).toContain(bears.id);
    expect(removed).toContain(blocker.id);
    expect(combat(b).attacking.has(other.id)).toBe(true);
  });

  it("an attacked planeswalker that stops being one stops being attacked; its attacker attacks nothing (CR 506.4c)", () => {
    const b = board();
    const walker = b.play(WALKER, 1);
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, { permanent: walker.id }]]);
    expect(combat(b).attackingPermanent.get(bears.id)).toBe(walker.id);
    block(b);
    becomeArtifact(b, walker);
    dealCombatDamage(b.game);
    expect(combat(b).attacking.has(bears.id)).toBe(true);
    expect(combat(b).attackingPermanent.get(bears.id)).toBe(NO_OBJECT);
    expect(player(b.game, 1).life).toBe(40);
  });

  it("an attacked planeswalker whose controller changes stops being attacked", () => {
    const b = board();
    const walker = b.play(WALKER, 1);
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, { permanent: walker.id }]]);
    walker.controller = 0;
    checkRemovalFromCombat(b.game);
    expect(combat(b).attackingPermanent.get(bears.id)).toBe(NO_OBJECT);
  });

  it("gaining defender, being tapped, or losing power doesn't remove a declared attacker", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, 1]]);
    b.script(BEARS, kw("Defender"));
    bears.tapped = true;
    expect(checkRemovalFromCombat(b.game)).toEqual([]);
    expect(combat(b).attacking.has(bears.id)).toBe(true);
  });

  it("regenerating an attacker removes it from combat (CR 701.15)", () => {
    const b = board();
    const bears = b.play(BEARS, 0);
    attack(b, [[bears, 1]]);
    bears.regenerationShields = 1;
    expect(destroy(b.game, bears)).toBe(false);
    expect(combat(b).attacking.has(bears.id)).toBe(false);
  });
});

/** An attacker blocked by `blockers`, as the record would hold it once blockers are declared. */
function blockedBy(b: Board, attacker: GameObject, blockers: GameObject[]): void {
  const c = newCombat();
  c.attacking.set(attacker.id, 1);
  c.controllers.set(attacker.id, attacker.controller);
  c.blockers.set(attacker.id, blockers.map((x) => x.id));
  for (const x of blockers) {
    c.blocking.set(x.id, [attacker.id]);
    c.controllers.set(x.id, x.controller);
  }
  if (blockers.length > 0) c.wasBlocked.add(attacker.id);
  c.blockersDeclared = true;
  b.game.combat = c;
}

/** The attacker's controller divides as told (a Map keyed by recipient index). */
function divider(b: Board, division: Record<number, number>): void {
  setAgent(b.game, 0, { assignCombatDamage: () => new Map(Object.entries(division).map(([k, v]) => [Number(k), v])) });
}

describe("dividing combat damage (CR 510.1c-d, 702.19b)", () => {
  it("an attacker may split its damage, or put it all on one blocker", () => {
    const b = board();
    const angel = b.play(ANGEL, 0);
    const one = b.play(BEARS, 1);
    const two = b.play(RUNECLAW, 1);
    blockedBy(b, angel, [one, two]);
    divider(b, { 0: 2, 1: 2 });
    dealCombatDamage(b.game);
    expect([one.damage, two.damage]).toEqual([2, 2]);

    const c = board();
    const a2 = c.play(ANGEL, 0);
    const x = c.play(BEARS, 1);
    const y = c.play(RUNECLAW, 1);
    blockedBy(c, a2, [x, y]);
    divider(c, { 1: 4 });
    dealCombatDamage(c.game);
    expect([x.damage, y.damage]).toEqual([0, 4]);
  });

  it("a division that doesn't spend every point, or names a stranger, is refused for the default (CR 510.1e)", () => {
    const b = board();
    const angel = b.play(ANGEL, 0);
    const one = b.play(BEARS, 1);
    const two = b.play(RUNECLAW, 1);
    blockedBy(b, angel, [one, two]);
    divider(b, { 0: 1 });
    dealCombatDamage(b.game);
    expect(one.damage + two.damage).toBe(4);

    const c = board();
    const a2 = c.play(ANGEL, 0);
    const only = c.play(BEARS, 1);
    blockedBy(c, a2, [only]);
    divider(c, { 7: 4 });
    dealCombatDamage(c.game);
    expect(only.damage).toBe(4);
  });

  it("the default is lethal in turn", () => {
    const b = board();
    const angel = b.play(ANGEL, 0);
    const one = b.play(BEARS, 1);
    const two = b.play(RUNECLAW, 1);
    blockedBy(b, angel, [one, two]);
    dealCombatDamage(b.game);
    expect([one.damage, two.damage]).toEqual([2, 2]);
  });

  it("trample may not spill before the blocker has lethal, may once it does, and may over-assign", () => {
    const early = board({ [RHOX]: ["Trample"] });
    const rhox = early.play(RHOX, 0);
    const bears = early.play(BEARS, 1);
    blockedBy(early, rhox, [bears]);
    divider(early, { 0: 1, 1: 4 });
    dealCombatDamage(early.game);
    expect(bears.damage).toBe(2); // refused: the default, lethal then over
    expect(player(early.game, 1).life).toBe(37);

    const later = board({ [RHOX]: ["Trample"] });
    const r2 = later.play(RHOX, 0);
    const b2 = later.play(BEARS, 1);
    blockedBy(later, r2, [b2]);
    divider(later, { 0: 4, 1: 1 });
    dealCombatDamage(later.game);
    expect(b2.damage).toBe(4);
    expect(player(later.game, 1).life).toBe(39);
  });

  it("deathtouch makes 1 lethal for trample; marked damage lowers lethal", () => {
    const b = board({ [RHOX]: ["Trample", "Deathtouch"] });
    const rhox = b.play(RHOX, 0);
    const angel = b.play(ANGEL, 1);
    blockedBy(b, rhox, [angel]);
    divider(b, { 0: 1, 1: 4 });
    dealCombatDamage(b.game);
    expect(player(b.game, 1).life).toBe(36);

    const c = board({ [RHOX]: ["Trample"] });
    const r2 = c.play(RHOX, 0);
    const hurt = c.play(ANGEL, 1);
    hurt.damage = 3;
    blockedBy(c, r2, [hurt]);
    divider(c, { 0: 1, 1: 4 });
    dealCombatDamage(c.game);
    expect(player(c.game, 1).life).toBe(36);
  });

  it("a blocker divides among the creatures it blocks; one gets everything; none gets nothing", () => {
    const b = board();
    const angel = b.play(ANGEL, 1);
    const one = b.play(BEARS, 0);
    const two = b.play(RUNECLAW, 0);
    const c = newCombat();
    c.attacking.set(one.id, 1);
    c.attacking.set(two.id, 1);
    c.blocking.set(angel.id, [one.id, two.id]);
    c.blockers.set(one.id, [angel.id]);
    c.blockers.set(two.id, [angel.id]);
    c.blockersDeclared = true;
    b.game.combat = c;
    setAgent(b.game, 1, { assignCombatDamage: () => new Map([[0, 1], [1, 3]]) });
    dealCombatDamage(b.game);
    expect([one.damage, two.damage]).toEqual([1, 3]);
  });
});
