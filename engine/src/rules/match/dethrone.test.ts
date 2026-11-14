/**
 * Attacking the player with the most life (CR 702.105a, dethrone). Ported
 * from tests/rules/test_cr702_dethrone_condition.py, on combat's real
 * Combat record.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board } from "../../../test/rules/board";
import type { Condition } from "../../ir";
import type { GameObject } from "../../kernel/gameobject";
import { newPlayer } from "../cr100_game_concepts/player";
import { newCombat } from "../cr500_turn_structure/combat";
import { ConditionKind } from "./enums";
import { holds, installMatch } from "./index";

const MOST_LIFE = { $: "Condition", kind: ConditionKind.ATTACKING_PLAYER_WITH_MOST_LIFE } as Condition;
let board: Board;

beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.players.push(newPlayer(2));
  board.game.turnOrder = [0, 1, 2];
  board.game.activePlayer = 0;
});

function attacking(defender: number): GameObject {
  const bears = board.play("Grizzly Bears", 0);
  const combat = newCombat();
  combat.attacking.set(bears.id, defender);
  board.game.combat = combat;
  return bears;
}
const holdsFor = (bears: GameObject) => holds(board.game, MOST_LIFE, { source: bears.id, controller: 0 });

describe("dethrone's condition (CR 702.105a)", () => {
  it("attacking the player with the most life", () => {
    board.game.players[2]!.life = 30;
    expect(holdsFor(attacking(1))).toBe(true);
  });

  it("a tie for the most counts", () => {
    expect(holdsFor(attacking(1))).toBe(true);
  });

  it("attacking someone with less doesn't", () => {
    board.game.players[1]!.life = 30;
    expect(holdsFor(attacking(1))).toBe(false);
  });

  it("the attacker's own life counts too", () => {
    board.game.players[0]!.life = 50;
    expect(holdsFor(attacking(1))).toBe(false);
  });

  it("not attacking is not attacking anyone", () => {
    expect(holdsFor(board.play("Grizzly Bears", 0))).toBe(false);
  });
});
