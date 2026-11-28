// mana-not-lost (rules/dummies.ts), on a real card: Birgi, God of
// Storytelling's "Whenever you cast a spell, add {R}. Until end of turn, you
// don't lose this mana as steps and phases end." The mana the trigger added
// is kept through step and phase ends (CR 500.4) and goes as the cleanup
// step ends; other mana empties as usual.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import { addMana, clearPool, manaKind, poolSnapshot } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, PassingAgent, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { IMPLEMENTED_DUMMIES } from "../../src/rules/dummies";
import { legalActions } from "../../src/rules/legality";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, new PassingAgent());
  return b;
}
function castBears(b: RealBoard): void {
  const bears = b.hand("Grizzly Bears", 0);
  addMana(player(b.game, 0).manaPool, manaKind(Color.GREEN), 2);
  const act = legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === bears.id)!;
  expect(perform(b.game, 0, act)).toBe(true);
  settle(b.game);
  // Birgi's trigger is above the Bears: resolve it only.
  resolveTop(b.game);
  settle(b.game);
}
const pool = (b: RealBoard) => poolSnapshot(player(b.game, 0).manaPool).map(([k, n]) => [k.color, k.untilEndOfTurn, n]);

describe("mana-not-lost: Birgi", () => {
  it("the {R} its trigger adds is kept until end of turn", () => {
    const b = board();
    b.play("Birgi, God of Storytelling // Harnfel, Horn of Bounty", 0);
    castBears(b);
    expect(pool(b)).toEqual([[Color.RED, true, 1]]);
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLUE), 1); // ordinary mana
    // A step or phase ends: only the ordinary mana is lost.
    clearPool(player(b.game, 0).manaPool, false, true);
    expect(pool(b)).toEqual([[Color.RED, true, 1]]);
    // The cleanup step ends: it goes too.
    clearPool(player(b.game, 0).manaPool, false, false);
    expect(pool(b)).toEqual([]);
  });

  it("the kept {R} still pays for a spell", () => {
    const b = board();
    b.play("Birgi, God of Storytelling // Harnfel, Horn of Bounty", 0);
    castBears(b);
    const bolt = b.hand("Lightning Bolt", 0);
    b.play("Grizzly Bears", 1);
    const act = legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === bolt.id);
    expect(act).toBeDefined();
    expect(perform(b.game, 0, act!)).toBe(true);
  });

  it("without the dummy, an ability's mana is ordinary", () => {
    const b = board();
    addMana(player(b.game, 0).manaPool, manaKind(Color.RED), 1);
    clearPool(player(b.game, 0).manaPool, false, true);
    expect(pool(b)).toEqual([]);
    expect(IMPLEMENTED_DUMMIES.has("mana-not-lost")).toBe(true);
  });
});
