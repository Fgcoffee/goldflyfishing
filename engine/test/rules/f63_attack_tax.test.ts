/**
 * F63: Norn's Annex taxes each attacker {W/P}. Against a swarm the total
 * passes the payment search's limit, where Python raises; the port pays the
 * symbols past the limit greedily, and the attack shrinks to what can be paid.
 */

import { beforeEach, describe, expect, it } from "vitest";
import type { ObjectId } from "../../src/kernel/ids";
import type { ManaCost } from "../../src/rules/mana/cost";
import { affordable, attackCost } from "../../src/rules/cr500_turn_structure/combat_costs";
import { installReplacements } from "../../src/rules/cr600_spells_and_abilities/replacement";
import { installMatch } from "../../src/rules/match";
import { RealBoard } from "./real_cards";

let board: RealBoard;

beforeEach(() => {
  board = new RealBoard();
  installMatch();
  installReplacements();
});

function swarmAgainstTheAnnex(attackers: number): Map<ObjectId, ManaCost> {
  board.play("Norn's Annex", 1);
  board.play("Plains", 0);
  board.play("Plains", 0);
  const costs = new Map<ObjectId, ManaCost>();
  for (let i = 0; i < attackers; i++) {
    const bear = board.play("Grizzly Bears", 0);
    costs.set(bear.id, attackCost(board.game, bear, 1));
  }
  return costs;
}

describe("F63: a {W/P} tax for each of a swarm of attackers", () => {
  it("each attacker is taxed {W/P}", () => {
    const costs = swarmAgainstTheAnnex(16);
    for (const mana of costs.values()) expect(mana.symbols.map((s) => s.text)).toEqual(["{W/P}"]);
  });

  it("16 attackers at 40 life: all of them, two with Plains and fourteen with 28 life", () => {
    board.game.players[0]!.life = 40;
    const costs = swarmAgainstTheAnnex(16);
    expect(affordable(board.game, 0, costs).size).toBe(16);
  });

  it("16 attackers at 20 life: fewer attack, rather than the engine crashing", () => {
    board.game.players[0]!.life = 20;
    const costs = swarmAgainstTheAnnex(16);
    // Two from the Plains, then 2 life each while at least 1 life is left: 9 more.
    expect(affordable(board.game, 0, costs).size).toBe(11);
  });
});
