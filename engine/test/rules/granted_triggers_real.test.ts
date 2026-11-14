// Ported from tests/rules/test_cr603_granted_triggers.py, on real cards
// (schema/fixtures): triggered abilities a permanent was granted trigger
// like printed ones (CR 613.1f, 603.10a), and a granted "return it" finds
// the card the creature became (CR 400.7e). Python builds granted persist
// and modular from cr702_keyword_impl (no TS counterpart, no fixture grants
// them), so those cases stay in Python.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { counterCount } from "../../src/kernel/gameobject";
import { destroy } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, perform, PassingAgent, settle } from "../../src/rules/cr100_game_concepts/priority";
import { declareAttackers } from "../../src/rules/cr500_turn_structure/combat";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { moveObject } from "../../src/rules/zones";
import { RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  return b;
}
const bears = (game: Game): GameObject[] =>
  [...game.objects.values()].filter((o) => !o.supersededBy && (o.card as { name?: string } | null)?.name === "Grizzly Bears");
function resolveEverything(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 10 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
function cast(b: RealBoard, name: string, target: number): void {
  addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 2);
  addMana(player(b.game, 0).manaPool, manaKind(Color.RED), 1);
  const card = b.hand(name);
  const action = legalActions(b.game, 0).find((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id)!;
  expect(perform(b.game, 0, { ...action, targets: [[target]] })).toBe(true);
  resolveEverything(b);
}

describe("a granted dies trigger (Abnormal Endurance)", () => {
  it("returns the creature, tapped, as a new object", () => {
    const b = board();
    const [creature] = [b.play("Grizzly Bears")];
    cast(b, "Abnormal Endurance", creature!.id);
    const [buffed] = bears(b.game);
    cast(b, "Shock", buffed!.id);
    const [returned] = bears(b.game);
    expect(returned!.zone).toBe(Zone.BATTLEFIELD);
    expect(returned!.id).not.toBe(creature!.id);
    expect(returned!.tapped).toBe(true);
  });

  it("without the grant the creature stays dead", () => {
    const b = board();
    const creature = b.play("Grizzly Bears");
    cast(b, "Shock", creature.id);
    expect(bears(b.game).map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
  });
});

describe("granted keyword triggers (CR 702.90b, 702.93a)", () => {
  it("Sublime Archangel: a lone attacker gets one exalted trigger per creature that has it", () => {
    const b = board();
    b.game.phase = Phase.COMBAT;
    b.game.step = Step.DECLARE_ATTACKERS;
    b.play("Sublime Archangel");
    const attacker = b.play("Grizzly Bears");
    b.play("Grizzly Bears");
    b.game.agents.set(0, Object.assign(new PassingAgent(), { declareAttackers: () => new Map([[attacker.id, 1]]) }));
    b.game.pendingTriggers.length = 0;
    declareAttackers(b.game);
    expect(b.game.pendingTriggers).toHaveLength(3);
  });

  function mikaeus(): RealBoard {
    const b = board();
    b.play("Mikaeus, the Unhallowed");
    return b;
  }

  it("a granted undying finds the card in the graveyard and returns it with a +1/+1 counter", () => {
    const b = mikaeus();
    destroy(b.game, b.play("Grizzly Bears"));
    resolveEverything(b);
    const [returned] = bears(b.game);
    expect(returned!.zone).toBe(Zone.BATTLEFIELD);
    expect(counterCount(returned!, "+1/+1")).toBe(1);
  });

  it("doesn't follow a card that moved on (CR 400.7e)", () => {
    const b = mikaeus();
    destroy(b.game, b.play("Grizzly Bears"));
    settle(b.game);
    const [dead] = bears(b.game);
    moveObject(b.game, dead!, Zone.EXILE);
    resolveEverything(b);
    expect(bears(b.game).map((o) => o.zone)).toEqual([Zone.EXILE]);
  });

  it("doesn't happen twice: the second time it had a +1/+1 counter (CR 603.10a)", () => {
    const b = mikaeus();
    destroy(b.game, b.play("Grizzly Bears"));
    resolveEverything(b);
    const [returned] = bears(b.game);
    destroy(b.game, returned!);
    resolveEverything(b);
    expect(bears(b.game).map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
  });
});
