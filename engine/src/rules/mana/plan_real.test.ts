/**
 * Paying for a spell with real lands (CR 601.2g-h). Ported from
 * tests/rules/test_cr601_mana_plan.py, which uses real parsed cards because
 * the bug was in how real lands were read: here the IR in schema/fixtures/,
 * through the rules session's castSpell, legalActions and mana host.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { RealBoard } from "../../../test/rules/real_cards";
import { Phase, Step } from "../../kernel/enums";
import type { GameObject } from "../../kernel/gameobject";
import { action, ActionKind } from "../cr100_game_concepts/priority";
import { castSpell } from "../cr600_spells_and_abilities/casting";
import { CastError } from "../cr600_spells_and_abilities/cast_error";
import * as host from "../cr600_spells_and_abilities/mana_host";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { legalActions } from "../legality";
import { installMatch } from "../match";
import { parseCost } from "./cost";
import { poolTotal } from "./pool";

let board: RealBoard;
const cast = (card: GameObject) => castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }));
const offered = (card: GameObject) => legalActions(board.game, 0).some((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);

beforeEach(() => {
  board = new RealBoard();
  installMatch();
  installReplacements();
  board.game.activePlayer = 0;
  board.game.priorityPlayer = 0;
  board.game.phase = Phase.PRECOMBAT_MAIN;
  board.game.step = Step.MAIN;
});

describe("which mana abilities pay for a spell (CR 601.2g-h)", () => {
  it("a pain land pays for the colour it can make, and the pain is paid", () => {
    const land = board.play("Karplusan Forest");
    const bolt = board.hand("Lightning Bolt");
    expect(offered(bolt)).toBe(true);
    cast(bolt);
    expect(land.tapped).toBe(true);
    expect(board.game.players[0]!.life).toBe(39); // CR 107.4
  });

  it("a basic land is tapped before a pain land", () => {
    const pain = board.play("Karplusan Forest");
    const mountain = board.play("Mountain");
    cast(board.hand("Lightning Bolt"));
    expect(mountain.tapped && !pain.tapped).toBe(true);
    expect(board.game.players[0]!.life).toBe(40);
  });

  it("a modal land makes the colour the cost needs", () => {
    board.play("Jungle Hollow");
    board.play("Grizzly Bears");
    const growth = board.hand("Giant Growth");
    expect(offered(growth)).toBe(true);
    cast(growth);
    expect(board.game.stack.length).toBe(1);
  });

  it("'any colour' is the colour the cost needs", () => {
    board.play("Birds of Paradise");
    cast(board.hand("Lightning Bolt"));
    expect(board.game.stack.length).toBe(1);
  });

  it("only what the cost needs is tapped", () => {
    const forests = [board.play("Forest"), board.play("Forest"), board.play("Forest")];
    const mountain = board.play("Mountain");
    const plan = host.planPaymentFor(board.game, 0, parseCost("{R}"))!;
    expect(plan.map((o) => o.source)).toEqual([mountain.id]);
    expect(forests.some((f) => f.tapped)).toBe(false);
  });

  it("a Signet is paid for by another source", () => {
    board.play("Forest");
    board.play("Rakdos Signet");
    board.play("Grizzly Bears", 1);
    expect(offered(board.hand("Terminate"))).toBe(true);
  });

  it("colours nothing can make are not offered", () => {
    for (let i = 0; i < 5; i++) board.play("Forest");
    expect(offered(board.hand("Lightning Bolt"))).toBe(false);
  });

  it("a rewound cast untaps what it tapped (CR 601.2h)", () => {
    const forest = board.play("Forest");
    const bolt = board.hand("Lightning Bolt");
    const wrong = host.manaOptions(board.game, 0)[0]!.slice(0, 1);
    const spy = vi.spyOn(host, "planPaymentFor").mockReturnValue(wrong);
    try {
      expect(() => cast(bolt)).toThrow(CastError);
      expect(spy).toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
    expect(forest.tapped).toBe(false);
    expect(poolTotal(board.game.players[0]!.manaPool)).toBe(0);
  });
});
