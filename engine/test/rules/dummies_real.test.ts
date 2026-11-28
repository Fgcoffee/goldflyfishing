// Dummy abilities the engine now carries out (rules/dummies.ts), on real
// cards (schema/fixtures): the parser's DUMMY effects by `rule`.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind } from "../../src/rules/cr100_game_concepts/priority";
import { legalActions } from "../../src/rules/legality";
import { DEFAULT_TURN_OPTIONS, turnBasedActions } from "../../src/rules/cr500_turn_structure/turn";
import { beginGameOnBattlefield, IMPLEMENTED_DUMMIES } from "../../src/rules/dummies";
import { Zone } from "../../src/kernel/enums";
import { RealBoard } from "./real_cards";

function untapStepOf(b: RealBoard, active: number): void {
  b.game.activePlayer = active;
  b.game.phase = Phase.BEGINNING;
  b.game.step = Step.UNTAP;
  turnBasedActions(b.game, Step.UNTAP, DEFAULT_TURN_OPTIONS);
}

describe("untap-during-others-untap-step (CR 502.3)", () => {
  it("Seedborn Muse: its controller's permanents untap in another player's untap step", () => {
    const b = new RealBoard();
    b.play("Seedborn Muse", 0);
    const forest = b.play("Forest", 0);
    const bears = b.play("Grizzly Bears", 0);
    const theirs = b.play("Forest", 1);
    forest.tapped = bears.tapped = theirs.tapped = true;
    untapStepOf(b, 1);
    expect([forest.tapped, bears.tapped, theirs.tapped]).toEqual([false, false, false]);
  });

  it("without it, nothing of player 0's untaps in player 1's untap step", () => {
    const b = new RealBoard();
    const forest = b.play("Forest", 0);
    forest.tapped = true;
    untapStepOf(b, 1);
    expect(forest.tapped).toBe(true);
  });

  it("Unwinding Clock untaps artifacts only", () => {
    const b = new RealBoard();
    b.play("Unwinding Clock", 0);
    const ring = b.play("Sol Ring", 0);
    const bears = b.play("Grizzly Bears", 0);
    ring.tapped = bears.tapped = true;
    untapStepOf(b, 1);
    expect([ring.tapped, bears.tapped]).toEqual([false, true]);
  });

  it("is listed as implemented", () => {
    expect(IMPLEMENTED_DUMMIES.has("untap-during-others-untap-step")).toBe(true);
  });
});

describe("activation-cost-change (CR 601.2f): the channel lands", () => {
  const channel = (b: RealBoard, card: ReturnType<RealBoard["hand"]>) =>
    legalActions(b.game, 0).filter((a) => a.kind === ActionKind.ACTIVATE_ABILITY && a.source === card.id);
  function setUp(legendary: boolean): [RealBoard, ReturnType<RealBoard["hand"]>] {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    b.play("Grizzly Bears", 1);
    b.play("Sol Ring", 1); // a target: an opponent's artifact
    if (legendary) b.play("Kenrith, the Returned King", 0);
    const boseiju = b.hand("Boseiju, Who Endures");
    addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 1);
    return [b, boseiju];
  }
  it("Boseiju's channel ({1}{G}) costs {G} with a legendary creature", () => {
    const [b, boseiju] = setUp(true);
    expect(channel(b, boseiju).length).toBeGreaterThan(0);
  });
  it("and isn't affordable with one mana without one", () => {
    const [b, boseiju] = setUp(false);
    expect(channel(b, boseiju)).toHaveLength(0);
  });
});

describe("begin-game-on-battlefield (CR 103.6): the Leylines", () => {
  it("Leyline of the Void in the opening hand begins the game on the battlefield; other cards stay", () => {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    const leyline = b.hand("Leyline of the Void", 1);
    const bears = b.hand("Grizzly Bears", 1);
    beginGameOnBattlefield(b.game);
    const now = b.game.objects.get(leyline.supersededBy || leyline.id)!;
    expect(now.zone).toBe(Zone.BATTLEFIELD);
    expect(now.controller).toBe(1);
    expect(bears.zone).toBe(Zone.HAND);
  });
  it("an agent may decline it", () => {
    const b = new RealBoard();
    b.game.agents.set(0, { chooseOptional: () => false } as never);
    const leyline = b.hand("Leyline of the Void", 0);
    beginGameOnBattlefield(b.game);
    expect(leyline.zone).toBe(Zone.HAND);
  });
});
