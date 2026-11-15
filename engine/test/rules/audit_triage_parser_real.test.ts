// Triage of the parser-owned findings in audit/AUDIT_LOG.md against the IR
// as exported now (schema/fixtures), 15 Nov. Each test states what the card
// says and checks the reading; `it.fails` marks a finding still open
// (KNOWN: Fxx), and comes out when the reading is fixed. F28 (overload) is
// fixed in the engine from the keyword (rules b28f152; audit_triage_real.test.ts).
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { complete, type Ability, type Effect } from "../../src/ir";
import { AbilityKindValues as AK, CostKindValues as CK, EffectKindValues as EK, PlayerScopeValues as PS, ValueKindValues as VK, ZoneValues as Z } from "../../src/ir/types";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { draw } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { RealBoard, REAL_ABILITIES, realCard, realDocument as doc } from "./real_cards";

/** A card's abilities as the engine reads them (face 0), defaults filled in. */
const abilities = (name: string): Ability[] => REAL_ABILITIES.abilitiesFor(realCard(name), 0).map((a) => complete(a) as Ability);
const ability = (name: string, text: string): Ability => {
  const found = abilities(name).find((a) => (a.text ?? "").includes(text));
  if (found === undefined) throw new Error(`${name}: no ability with "${text}"`);
  return found;
};
/** Every effect in an ability, depth first. */
function effects(a: Ability): Effect[] {
  const out: Effect[] = [];
  const walk = (list: readonly Effect[] | undefined) => {
    for (const e of list ?? []) {
      out.push(e);
      walk(e.children as Effect[] | undefined);
    }
  };
  walk(a.effects as Effect[]);
  return out;
}
const ofKind = (a: Ability, kind: number) => effects(a).filter((e) => e.kind === kind);

describe("fixed: the reading matches the card", () => {
  it("F25 Ertai's Trickery stays unread rather than counter on its own kicker", () => {
    expect(ability("Ertai's Trickery", "Counter target spell").unparsed).toBe(true);
  });

  it("F31 Counterspell targets a spell, never an ability", () => {
    const [counter] = ofKind(ability("Counterspell", "Counter"), EK.COUNTER_SPELL!);
    expect(counter!.is_targeted).toBe(true);
    expect((counter!.targets as { is_ability?: boolean }).is_ability).toBe(false);
  });

  it("F33 Turn to Frog lasts until end of turn, on the one target", () => {
    const frog = ability("Turn to Frog", "Until end of turn");
    const leaves = effects(frog).filter((e) => e.kind !== EK.SEQUENCE);
    expect(leaves.map((e) => e.duration)).toEqual([1, 1, 1]);
    expect(leaves.slice(1).every((e) => e.same_target === true)).toBe(true);
  });

  it("F43 Sly Instigator: one target creature an opponent controls, then goad it", () => {
    const sly = ability("Sly Instigator", "can't be blocked");
    expect(ofKind(sly, EK.RESTRICTION!)[0]!.is_targeted).toBe(true);
    expect((ofKind(sly, EK.GOAD!)[0]!.targets as { remembered?: boolean }).remembered).toBe(true);
  });

  it("F46 Harnesser of Storms stays unread rather than read 'noncreature or Otter' as AND", () => {
    expect(ability("Harnesser of Storms", "noncreature or Otter").unparsed).toBe(true);
  });

  it("F51 Ulamog, the Ceaseless Hunger's cast trigger takes two targets", () => {
    const [exile] = ofKind(ability("Ulamog, the Ceaseless Hunger", "two target permanents"), EK.EXILE!);
    expect(exile!.is_targeted).toBe(true);
    expect((exile!.targets as { count?: { constant?: number } }).count?.constant).toBe(2);
  });

  it("F58 Replication Technique makes a token copy of the one target permanent you control", () => {
    const [create] = ofKind(ability("Replication Technique", "token that's a copy"), EK.CREATE_TOKEN!);
    expect(create!.is_targeted).toBe(true);
    expect((create!.token as { copy_of?: unknown }).copy_of).toBeTruthy();
  });

  it("F59 Soul-Guide Lantern: today's Oracle says 'from a graveyard', and that's the reading", () => {
    const enters = ability("Soul-Guide Lantern", "When this artifact enters");
    expect(enters.text).toContain("from a graveyard");
    expect(ofKind(enters, EK.EXILE!)[0]!.is_targeted).toBe(true);
  });

  it("F4 Crew taps creatures with total power N, not N creatures", () => {
    const crew = ability("Cultivator's Caravan", "Crew 3");
    const tap = (crew.cost!.components ?? []).find((c) => c.kind === CK.TAP_OTHER)!;
    expect(tap.total_power).toBe(true);
    expect(tap.amount?.constant).toBe(3);
  });

  it("F49 Llanowar Wastes deals its damage on either coloured mana", () => {
    const pain = ability("Llanowar Wastes", "{B} or {G}");
    expect(effects(pain)[0]!.kind).toBe(EK.CHOOSE_MODE);
    expect((pain.effects as Effect[]).at(-1)!.kind).toBe(EK.DAMAGE);
  });

  it("F49 targets read as targets: Chainwhip Cyclops, Phase Dolphin, Insufferable Balladeer", () => {
    for (const [name, text] of [["Chainwhip Cyclops", "can't block"], ["Phase Dolphin", "can't be blocked"], ["Insufferable Balladeer", "can't block"]] as const) {
      expect(ofKind(ability(name, text), EK.RESTRICTION!)[0]!.is_targeted).toBe(true);
    }
  });

  it("F49 Jaya Ballard discards up to three, then draws that many", () => {
    const jaya = ability("Jaya Ballard", "Discard up to three");
    const discard = ofKind(jaya, EK.DISCARD!)[0]!;
    expect((discard.targets as { count?: { constant?: number }; up_to?: boolean }).count?.constant).toBe(3);
    expect((discard.targets as { up_to?: boolean }).up_to).toBe(true);
    expect(ofKind(jaya, EK.DRAW!)[0]!.amount?.kind).toBe(VK.EVENT_AMOUNT);
  });

  it("F49 builders: echo sacrifices if unpaid, outlast taps, cumulative upkeep pays per age counter, detain takes up to two until your next turn", () => {
    const echo = ability("Avalanche Riders", "Echo");
    expect(ofKind(echo, EK.IF_YOU_DONT!).length).toBe(1);
    expect(ofKind(echo, EK.SACRIFICE!).length).toBe(1);
    expect((ability("Ainok Bond-Kin", "Outlast").cost!.components ?? []).some((c) => c.kind === CK.TAP_SELF)).toBe(true);
    expect(ofKind(ability("Mystic Remora", "Cumulative upkeep"), EK.PAY_COST!)[0]!.amount?.kind).toBe(VK.COUNTERS);
    const detain = ofKind(ability("Lyev Decree", "Detain"), EK.RESTRICTION!)[0]!;
    expect(detain.duration).toBe(3);
    expect((detain.targets as { count?: { constant?: number }; up_to?: boolean }).count?.constant).toBe(2);
  });

  it("F34 embalm stays unread (Sacred Cat) rather than misbuilt, and the card says so", () => {
    const embalm = ability("Sacred Cat", "Embalm");
    expect(effects(embalm).map((e) => e.kind)).toEqual([EK.UNPARSED]);
    // The face isn't "fully read" with an unread placeholder in it (codec.py _unread).
    expect(doc("Sacred Cat").faces[0]!.fully_read).toBe(false);
  });

});

describe("fixed after the triage", () => {
  it("F49 Relentless Pursuit: a creature card AND/OR a land card - up to one of each", () => {
    const moves = ofKind(ability("Relentless Pursuit", "and/or"), EK.MOVE_ZONE!).filter((e) => e.zone === Z.HAND);
    expect(moves).toHaveLength(2);
    for (const move of moves) {
      const t = move.targets as { count?: { constant?: number }; up_to?: boolean; from_pile?: boolean };
      expect([t.count?.constant, t.up_to, t.from_pile]).toEqual([1, true, true]);
    }
    const types = (m: Effect) => { const t = m.targets as { types_any?: number; types_all?: number }; return (t.types_all ?? 0) | (t.types_any ?? 0); };
    expect(moves.map(types)).toEqual([4, 16]); // creature, land
  });

  it("F49 Chandra, Spark Hunter's +2: you may choose one of two, then 'if you do, draw'", () => {
    const plus = ability("Chandra, Spark Hunter", "+2");
    const top = plus.effects as Effect[];
    expect(top.map((e) => e.kind)).toEqual([EK.OPTIONAL, EK.REFLEXIVE_TRIGGER]);
    const choice = top[0]!.children![0]!;
    expect(choice.kind).toBe(EK.CHOOSE_MODE);
    expect(choice.children!.map((e) => e.kind)).toEqual([EK.SACRIFICE, EK.DISCARD]);
  });

  it("F49 Arcade Cabinet's 'each of up to four target creatures' are targets", () => {
    expect(ofKind(ability("Arcade Cabinet", "up to four target"), EK.ADD_COUNTERS!)[0]!.is_targeted).toBe(true);
  });

  it("F49 Fists of Flame: +1/+0 for each card drawn this turn, on the board", () => {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    for (let i = 0; i < 5; i++) createObject(b.game, realCard("Forest"), 0, Zone.LIBRARY);
    const bears = b.play("Grizzly Bears", 0);
    draw(b.game, 0, 2);
    const fists = b.hand("Fists of Flame");
    addMana(player(b.game, 0).manaPool, manaKind(Color.RED), 2);
    const cast = legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === fists.id)!;
    expect(perform(b.game, 0, cast)).toBe(true);
    settle(b.game);
    while (b.game.stack.length > 0) {
      resolveTop(b.game);
      settle(b.game);
    }
    // Two drawn before, one by Fists itself: +3/+0.
    expect(characteristicsOf(b.game, bears).power).toBe(5);
  });});

describe("F37 Toxic Deluge: 'pay X life' is an additional cost (CR 601.2b, 601.2h)", () => {
  it("is read as the spell's additional cost, with X the spell's own", () => {
    const deluge = ability("Toxic Deluge", "pay X life");
    const pay = (deluge.additional_cost?.cost?.components ?? [])[0]!;
    expect(pay.kind).toBe(CK.PAY_LIFE);
    expect(pay.amount?.kind).toBe(VK.X);
    expect(ofKind(deluge, EK.LOSE_LIFE!)).toEqual([]);
  });

  it.fails("cast with X = 3: 3 life is paid as it's cast, and creatures get -3/-3", () => {
    // KNOWN: F37 engine half (rules) - componentAmount (cost_checks.ts) evaluates a
    // spell's additional cost without the spell as its source, so X is 0; and legality
    // offers no X for a spell whose only X is in an additional cost (CR 601.2b).
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    const giant = b.play("Grizzly Bears", 1);
    const deluge = b.hand("Toxic Deluge");
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 1);
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 2);
    const life = player(b.game, 0).life;
    const cast = legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === deluge.id)!;
    expect(cast).toBeDefined();
    expect(perform(b.game, 0, { ...cast, xValue: 3 })).toBe(true);
    // Paid while casting, before anything resolves.
    expect(player(b.game, 0).life).toBe(life - 3);
    settle(b.game);
    while (b.game.stack.length > 0) {
      resolveTop(b.game);
      settle(b.game);
    }
    expect(giant.zone === Zone.BATTLEFIELD && !giant.supersededBy).toBe(false);
  });
});

describe("open: the reading doesn't match the card", () => {





  it.fails("F3 Sudden Impact and F49 Viseling count only that player's hand", () => {
    // KNOWN: F3 - the hand count has no owner, so it counts every hand.
    for (const [name, text] of [["Sudden Impact", "that player's hand"], ["Viseling", "their hand"]] as const) {
      const amount = effects(ability(name, text)).find((e) => e.kind === EK.DAMAGE)!.amount!;
      const counted = JSON.stringify(amount);
      // An owner on the counted cards, or a hand count of one player.
      expect(/"owner":[1-9]/.test(counted) || amount.kind === VK.CARDS_IN_HAND).toBe(true);
    }
  });

  it.fails("F49 Borrowed Knowledge's first mode counts target opponent's hand, not every opponent's", () => {
    // KNOWN: F49 - "target opponent" is read as every opponent (controller: OPPONENT, untargeted).
    const draw = ofKind(ability("Borrowed Knowledge", "Choose one"), EK.DRAW!)[0]!;
    expect(JSON.stringify(draw.amount)).toContain(`"scope":${PS.TARGET_OPPONENT}`);
  });

  it.fails("F17 Tannuk: 'Artifact cards and red creature cards' - red applies to the creatures only", () => {
    // KNOWN: F17 - the adjective is distributed over the list.
    const grant = ofKind(ability("Tannuk, Steadfast Second", "warp"), EK.GRANT_ABILITY!)[0]!;
    expect((grant.targets as { colors_any?: number }).colors_any ?? 0).toBe(0);
  });

  it.fails("Turn to Frog becomes a BLUE Frog (new: the colour is lost)", () => {
    const frog = ability("Turn to Frog", "blue Frog");
    expect(ofKind(frog, EK.SET_COLOR!).length + effects(frog).filter((e) => (e.colors ?? 0) !== 0).length).toBeGreaterThan(0);
  });
});
