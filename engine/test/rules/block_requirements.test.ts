// Ported from tests/rules/test_block_requirements.py: blocking requirements
// (CR 509.1c) against restrictions, menace and the defender's own choices.
import { describe, expect, it } from "vitest";
import type { Ability } from "../../src/ir";
import { CardType, Phase } from "../../src/kernel/enums";
import { permanents } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { canBlockAtAll, declareAttackers, type Combat } from "../../src/rules/cr500_turn_structure/combat";
import {
  Act,
  BLOCKED_BY_ALL_ABLE,
  BLOCKS_IF_ABLE,
  blockObligations,
  enforceBlockRequirements,
  MUST_BE_BLOCKED,
  obligationSatisfied,
  registerStanding,
} from "../../src/rules/cr500_turn_structure/restrictions";
import { ir } from "../../src/rules/ir_read";
import { setAgent } from "./agent_util";
import { Board, CARDS, cardDef } from "./board";

const kw = (keyword: string): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword, text: keyword });
const define = (name: string, power: number, toughness: number): string => {
  CARDS[name] = cardDef(name, { types: CardType.CREATURE, power: String(power), toughness: String(toughness) });
  return name;
};
const BEARS = define("Req Bears", 2, 2);
const GIANT = define("Req Giant", 3, 3);
const SPIDER = define("Req Spider", 2, 4);
const WURM = define("Req Wurm", 6, 4);
const ANGEL = define("Req Angel", 4, 4);

function board(scripts: Record<string, string[]>): Board {
  const b = new Board();
  for (const [name, keywords] of Object.entries(scripts)) b.script(name, ...keywords.map(kw));
  b.game.activePlayer = 0;
  b.game.phase = Phase.COMBAT;
  return b;
}

function attackWith(b: Board, ...attackers: GameObject[]): Combat {
  setAgent(b.game, 0, { declareAttackers: () => new Map(attackers.map((a) => [a.id, 1])) });
  declareAttackers(b.game);
  return b.game.combat as Combat;
}

function enforce(b: Board, combat: Combat, proposal: Map<number, number[]> = new Map()) {
  const candidates = permanents(b.game, 1).filter((o) => canBlockAtAll(b.game, o));
  return enforceBlockRequirements(b.game, combat, 1, candidates, proposal);
}

const blocks = (result: ReadonlyMap<number, readonly number[]>, blocker: GameObject): number[] => [...(result.get(blocker.id) ?? [])];

describe("blocking requirements (CR 509.1c)", () => {
  it("a board with no requirements passes the proposal through untouched", () => {
    const b = board({});
    const bears = b.play(BEARS, 0);
    const giant = b.play(GIANT, 1);
    const combat = attackWith(b, bears);
    const proposal = new Map([[giant.id, [bears.id]]]);
    expect([...enforce(b, combat, proposal)]).toEqual([...proposal]);
  });

  it("a creature that must block is not forced onto an attacker it can't block", () => {
    const b = board({ [ANGEL]: ["Flying"], [GIANT]: [BLOCKS_IF_ABLE] });
    const angel = b.play(ANGEL, 0);
    b.play(GIANT, 1);
    expect(enforce(b, attackWith(b, angel)).size).toBe(0);
  });

  it("a creature that blocks if able is forced in, keeping the attacker its controller chose", () => {
    const b = board({ [GIANT]: [BLOCKS_IF_ABLE] });
    const bears = b.play(BEARS, 0);
    const other = b.play(SPIDER, 0);
    const giant = b.play(GIANT, 1);
    const combat = attackWith(b, bears, other);
    expect(blocks(enforce(b, combat), giant)).toEqual([bears.id]);
    expect(blocks(enforce(b, combat, new Map([[giant.id, [other.id]]])), giant)).toEqual([other.id]);
  });

  it("a tapped creature is under no requirement; a can't-block restriction beats it", () => {
    const b = board({ [GIANT]: [BLOCKS_IF_ABLE] });
    const bears = b.play(BEARS, 0);
    const giant = b.play(GIANT, 1);
    giant.tapped = true;
    expect(enforce(b, attackWith(b, bears)).size).toBe(0);

    const c = board({ [GIANT]: [BLOCKS_IF_ABLE] });
    const attacker = c.play(BEARS, 0);
    c.play(GIANT, 1);
    registerStanding(c.game, { $: "Restriction", act: Act.BLOCK as never, subject: { $: "ObjectFilter", types_all: ir("CardType.CREATURE") } });
    c.refresh();
    expect(enforce(c, attackWith(c, attacker)).size).toBe(0);
  });

  it("an attacker that must be blocked gets one blocker; two such attackers both get blocked", () => {
    const b = board({ [BEARS]: [MUST_BE_BLOCKED] });
    const bears = b.play(BEARS, 0);
    b.play(GIANT, 1);
    b.play(SPIDER, 1);
    const result = enforce(b, attackWith(b, bears));
    expect(result.size).toBe(1);

    const c = board({ [BEARS]: [MUST_BE_BLOCKED] });
    const one = c.play(BEARS, 0);
    const two = c.play(BEARS, 0);
    c.play(GIANT, 1);
    c.play(SPIDER, 1);
    const both = enforce(c, attackWith(c, one, two));
    expect(new Set([...both.values()].flat())).toEqual(new Set([one.id, two.id]));
  });

  it("every creature able to block a lure does; one unable isn't forced; a chosen block is pulled off", () => {
    const b = board({ [BEARS]: [BLOCKED_BY_ALL_ABLE] });
    const bears = b.play(BEARS, 0);
    const giant = b.play(GIANT, 1);
    const spider = b.play(SPIDER, 1);
    const result = enforce(b, attackWith(b, bears));
    expect(blocks(result, giant)).toEqual([bears.id]);
    expect(blocks(result, spider)).toEqual([bears.id]);

    const c = board({ [ANGEL]: ["Flying", BLOCKED_BY_ALL_ABLE], [SPIDER]: ["Reach"] });
    const angel = c.play(ANGEL, 0);
    const ground = c.play(GIANT, 1);
    const reach = c.play(SPIDER, 1);
    const flown = enforce(c, attackWith(c, angel));
    expect(blocks(flown, reach)).toEqual([angel.id]);
    expect(blocks(flown, ground)).toEqual([]);

    const d = board({ [BEARS]: [BLOCKED_BY_ALL_ABLE] });
    const lure = d.play(BEARS, 0);
    const wurm = d.play(WURM, 0);
    const g = d.play(GIANT, 1);
    expect(blocks(enforce(d, attackWith(d, lure, wurm), new Map([[g.id, [wurm.id]]])), g)).toEqual([lure.id]);
  });

  it("CR 509.1c's example: menace makes the second creature block as well; with nobody to help, nothing", () => {
    const b = board({ [BEARS]: ["Menace"], [GIANT]: [BLOCKS_IF_ABLE] });
    const bears = b.play(BEARS, 0);
    const giant = b.play(GIANT, 1);
    const wurm = b.play(WURM, 1);
    const result = enforce(b, attackWith(b, bears));
    expect(blocks(result, giant)).toEqual([bears.id]);
    expect(blocks(result, wurm)).toEqual([bears.id]);

    const c = board({ [BEARS]: ["Menace"], [GIANT]: [BLOCKS_IF_ABLE] });
    const alone = c.play(BEARS, 0);
    c.play(GIANT, 1);
    expect(enforce(c, attackWith(c, alone)).size).toBe(0);
  });

  it("obligations are one per requirement", () => {
    const b = board({ [BEARS]: [BLOCKED_BY_ALL_ABLE] });
    const bears = b.play(BEARS, 0);
    const giant = b.play(GIANT, 1);
    const spider = b.play(SPIDER, 1);
    const obligations = blockObligations(b.game, attackWith(b, bears), 1, [giant, spider]);
    expect(obligations).toHaveLength(2);
    expect(obligations.map((o) => o.blockers)).toEqual([[giant.id], [spider.id]]);
    expect(obligations.every((o) => o.attackers.length === 1 && o.attackers[0] === bears.id)).toBe(true);
    expect(obligationSatisfied(obligations[0]!, new Map())).toBe(false);
    expect(obligationSatisfied(obligations[0]!, new Map([[giant.id, bears.id]]))).toBe(true);
  });
});
