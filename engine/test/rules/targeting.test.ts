// Ported from the targeting and mode tests of cr601_casting.py
// (tests/rules/test_cr601_casting.py, test_cr700_modes.py, test_cr115_targets.py):
// targeted_nodes order, settle_target_choices, distinct_assignment, modes.
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { setAgent } from "./agent_util";
import { playerTarget } from "../../src/kernel/ids";
import { chooseModes, legalModes, modalEffect } from "../../src/rules/cr600_spells_and_abilities/modes";
import {
  candidatesFor,
  distinctAssignment,
  settleTargetChoices,
  targetBounds,
  targetedNodes,
} from "../../src/rules/cr600_spells_and_abilities/targeting";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, effect, filter } from "./board";

const targeted = (fields: Partial<Effect> = {}): Effect => effect({ kind: ir("EffectKind.DESTROY"), targets: ALL_CREATURES, is_targeted: true, ...fields });
const charm = (...modes: Effect[]): Effect => effect({ kind: ir("EffectKind.CHOOSE_MODE"), children: modes });

describe("targeted nodes (CR 700.2c, 115.3)", () => {
  it("only chosen modes hold slots; same_target holds none", () => {
    const a = targeted({ text: "a" });
    const b = targeted({ text: "b" });
    const shared = targeted({ text: "shared", same_target: true });
    const effects = [charm(effect({ kind: ir("EffectKind.SEQUENCE"), children: [a, shared] }), b)];
    expect(targetedNodes(effects).map((e) => e.text)).toEqual(["a", "b"]);
    expect(targetedNodes(effects, [1]).map((e) => e.text)).toEqual(["b"]);
    expect(targetedNodes(effects, []).map((e) => e.text)).toEqual([]);
  });
});

describe("settling target choices (CR 601.2c)", () => {
  it("drops what was not offered and repeats; fills a required empty slot", () => {
    const effects = [targeted(), targeted()];
    expect(settleTargetChoices(effects, [[1, 2], [3]], [[9, 2, 2], []], { fillRequired: true })).toEqual([[2], [3]]);
  });

  it("'another target' avoids the earlier choice, solving jointly when boxed in", () => {
    const effects = [targeted(), targeted({ distinct_from_earlier_targets: true })];
    expect(distinctAssignment(effects, [[5, 6], [5]])).toEqual([6, 5]);
    expect(settleTargetChoices(effects, [[5, 6], [5]], [[5], []], { fillRequired: true })).toEqual([[6], [5]]);
    expect(distinctAssignment(effects, [[5], [5]])).toBeNull();
  });

  it("an up-to slot may stay empty", () => {
    const upTo = targeted({ targets: filter({ ...ALL_CREATURES, up_to: true }) });
    expect(settleTargetChoices([upTo], [[1]], [[]], { fillRequired: true })).toEqual([[]]);
  });

  it("bounds: 'two target creatures' is exactly two, 'up to two' none to two", () => {
    const board = new Board();
    const two = targeted({ targets: filter({ ...ALL_CREATURES, count: { $: "Value", constant: 2 } }) });
    const upToTwo = targeted({ targets: filter({ ...ALL_CREATURES, count: { $: "Value", constant: 2 }, up_to: true }) });
    expect(targetBounds(board.game, two)).toEqual([2, 2]);
    expect(targetBounds(board.game, upToTwo)).toEqual([0, 2]);
    expect(targetBounds(board.game, targeted())).toEqual([1, 1]);
    expect(settleTargetChoices([two], [[1, 2, 3]], [[3]], { fillRequired: true, bounds: [[2, 2]] })).toEqual([[3, 1]]);
  });
});

describe("candidates", () => {
  it("hexproof stops an opponent's source, not its controller's; players are offered for 'target player'", () => {
    const board = new Board();
    board.script("Serra Angel", { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Hexproof", text: "Hexproof" });
    const angel = board.play("Serra Angel", 1);
    const bears = board.play("Grizzly Bears", 1);
    const mine = board.play("Glorious Anthem", 0);
    const theirs = board.play("Ghostly Prison", 1);
    expect(candidatesFor(board.game, mine, targeted(), 0)).toEqual([bears.id]);
    expect(candidatesFor(board.game, theirs, targeted(), 1).sort()).toEqual([angel.id, bears.id].sort());
    const targetPlayer = effect({ kind: ir("EffectKind.DRAW"), is_targeted: true, players: { $: "PlayerFilter", scope: ir("PlayerScope.TARGET_OPPONENT") } });
    expect(candidatesFor(board.game, mine, targetPlayer, 0)).toEqual([playerTarget(1)]);
  });
});

describe("modes (CR 700.2)", () => {
  it("a mode with no legal target can't be chosen; the engine picks where the agent doesn't", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    const draw = effect({ kind: ir("EffectKind.DRAW"), amount: { $: "Value", constant: 1 } });
    const effects = [charm(targeted(), draw)];
    const modal = modalEffect(effects)!;
    expect(legalModes(board.game, source, modal, 0)).toEqual([1]);
    expect(chooseModes(board.game, source, effects, 0)).toEqual([1]);
    board.play("Grizzly Bears", 1);
    expect(legalModes(board.game, source, modal, 0)).toEqual([0, 1]);
    expect(chooseModes(board.game, source, effects, 0, { announced: [1, 1] })).toEqual([1]);
  });

  it("choose two, with the agent's choice cleaned to the budget", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    const modes = [0, 1, 2].map((n) => effect({ kind: ir("EffectKind.DRAW"), text: `m${n}` }));
    const effects = [effect({ ...charm(...modes), amount: { $: "Value", constant: 2 } })];
    setAgent(board.game, 0, { chooseModes: () => [2, 2, 0, 1] });
    expect(chooseModes(board.game, source, effects, 0)).toEqual([2, 0]);
  });
});
