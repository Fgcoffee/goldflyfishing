// Ported from tests/rules/test_cr603_trigger_correctness.py,
// test_cr603_simultaneous_deaths.py, test_cr603_delayed_remembered.py and
// test_cr603_trigger_events.py. Python reads the triggers from oracle text
// with the parser; there is no TS parser, so the IR is written by hand.
import { describe, expect, it } from "vitest";
import type { Ability, Condition, Effect, TriggerCondition } from "../../src/ir";
import { Color, Duration, Zone } from "../../src/kernel/enums";
import { event, EventKind } from "../../src/kernel/events";
import { createObject, player } from "../../src/kernel/game";
import { installMatchHooks } from "../../src/kernel/hooks";
import { addCounters, destroy } from "../../src/rules/cr100_game_concepts/actions";
import {
  checkStateTriggers,
  delayedTrigger,
  isTriggeredManaAbility,
  pendingTrigger,
  putTriggersOnStack,
  type PendingTrigger,
} from "../../src/rules/cr600_spells_and_abilities/triggers";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { emit } from "../../src/rules/emit";
import { ir } from "../../src/rules/ir_read";
import { amountOfColor } from "../../src/rules/mana/pool";
import { ALL_CREATURES, Board, CARDS, effect, filter } from "./board";
import { putCastTriggers } from "../../src/rules/cr700_additional_rules/paid_costs";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const YOU = { $: "PlayerFilter" as const, scope: ir("PlayerScope.YOU") };
const THIS = filter({ source_only: true });

function triggered(text: string, kinds: number[], fields: Partial<TriggerCondition> = {}, effects: Effect[] = [E("NOTHING")]): Ability {
  return {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    trigger: { $: "TriggerCondition", event_kinds: { $set: kinds }, text, ...fields },
    effects,
    text,
  };
}

const pending = (board: Board): PendingTrigger[] => board.game.pendingTriggers as PendingTrigger[];

function fired(board: Board, text: string): number {
  const count = pending(board).filter((p) => p.ability.text === text).length;
  board.game.pendingTriggers.length = 0;
  return count;
}

function watch(board: Board, ability: Ability, holder = "Grizzly Bears", controller = 0) {
  board.script(holder, ability);
  const obj = board.play(holder, controller);
  board.game.pendingTriggers.length = 0;
  return obj;
}

describe("when a trigger fires (CR 603.2)", () => {
  it("the end step is every player's; 'your end step' only yours (CR 603.2b)", () => {
    const board = new Board();
    const every = triggered("every end step", [EventKind.END_STEP]);
    const yours = triggered("your end step", [EventKind.END_STEP], { players: YOU });
    board.script("Grizzly Bears", every, yours);
    board.play("Grizzly Bears");
    emit(board.game, event(EventKind.END_STEP, { player: 1 }));
    expect(pending(board).map((p) => p.ability.text)).toEqual(["every end step"]);
    board.game.pendingTriggers.length = 0;
    emit(board.game, event(EventKind.END_STEP, { player: 0 }));
    expect(pending(board).map((p) => p.ability.text).sort()).toEqual(["every end step", "your end step"]);
  });

  it("a counter trigger keeps its kind, and 'a counter' is one occurrence per counter (CR 122.1, 603.2c)", () => {
    const board = new Board();
    const text = "Whenever a +1/+1 counter is put on this creature";
    const me = watch(board, triggered(text, [EventKind.COUNTER_ADDED], { subject: THIS, counter_kind: "+1/+1", each_counter: true }));
    addCounters(board.game, me, "-1/-1", 1);
    expect(fired(board, text)).toBe(0);
    addCounters(board.game, me, "+1/+1", 3);
    expect(fired(board, text)).toBe(3);
  });

  it("'only once each turn' (CR 603.2)", () => {
    const board = new Board();
    const text = "once each turn";
    watch(board, triggered(text, [EventKind.END_STEP], { once_each_turn: true }));
    emit(board.game, event(EventKind.END_STEP, { player: 0 }));
    emit(board.game, event(EventKind.END_STEP, { player: 1 }));
    expect(fired(board, text)).toBe(1);
  });

  it("an intervening if that fails means no trigger (CR 603.4)", () => {
    const board = new Board();
    const text = "if false";
    const never: Condition = { $: "Condition", kind: ir("ConditionKind.CONTROLS_MATCHING") };
    watch(board, triggered(text, [EventKind.END_STEP], { intervening_if: never }));
    installMatchHooks({ holds: () => false });
    emit(board.game, event(EventKind.END_STEP, { player: 0 }));
    expect(fired(board, text)).toBe(0);
  });

  it("triggers are noted in APNAP order (CR 603.3b)", () => {
    const board = new Board();
    board.game.activePlayer = 1;
    board.script("Grizzly Bears", triggered("end", [EventKind.END_STEP]));
    board.play("Grizzly Bears", 0);
    board.play("Grizzly Bears", 1);
    board.game.pendingTriggers.length = 0;
    emit(board.game, event(EventKind.END_STEP, { player: 1 }));
    expect(pending(board).map((p) => p.controller)).toEqual([1, 0]);
  });
});

describe("looking back in time (CR 603.10a)", () => {
  it("a creature's own 'when this dies' triggers from the battlefield it left", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const text = "When this creature dies";
    const me = watch(board, triggered(text, [EventKind.DIES], { subject: THIS }));
    destroy(board.game, me);
    expect(fired(board, text)).toBe(1);
  });

  it("a Blood Artist dying with another creature sees both die", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const text = "Whenever a creature dies";
    const artist = watch(board, triggered(text, [EventKind.DIES], { subject: ALL_CREATURES }), "Serra Angel");
    const bears = board.play("Grizzly Bears");
    // Destroyed one after the other, in one batch, as a destroy-all does.
    destroy(board.game, artist);
    destroy(board.game, bears);
    expect(fired(board, text)).toBe(2);
  });

  it("'one or more creatures die' triggers once for the batch, counting it (CR 603.2c)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const text = "Whenever one or more creatures die";
    watch(board, triggered(text, [EventKind.DIES], { subject: ALL_CREATURES, batched: true }), "Glorious Anthem");
    const a = board.play("Grizzly Bears");
    const b = board.play("Grizzly Bears");
    destroy(board.game, a);
    destroy(board.game, b);
    expect(pending(board)).toHaveLength(1);
    expect(pending(board)[0]!.event.amount).toBe(2);
  });
});

describe("delayed and state triggers (CR 603.7, 603.8)", () => {
  it("a delayed trigger fires once, the next time (CR 603.7b)", () => {
    const board = new Board();
    const anthem = board.play("Glorious Anthem");
    const trigger: TriggerCondition = { $: "TriggerCondition", event_kinds: { $set: [EventKind.END_STEP] }, text: "next end step" };
    board.game.delayedTriggers.push(delayedTrigger(trigger, [E("NOTHING")], 0, anthem.id));
    emit(board.game, event(EventKind.END_STEP, { player: 0 }));
    expect(fired(board, "next end step")).toBe(1);
    emit(board.game, event(EventKind.END_STEP, { player: 1 }));
    expect(fired(board, "next end step")).toBe(0);
    expect(board.game.delayedTriggers).toHaveLength(0);
  });

  it("a delayed trigger about one object ignores the others (CR 603.7c)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const anthem = board.play("Glorious Anthem");
    const watched = board.play("Grizzly Bears");
    const other = board.play("Grizzly Bears");
    const trigger: TriggerCondition = {
      $: "TriggerCondition",
      event_kinds: { $set: [EventKind.DIES] },
      subject: filter({ remembered: true }),
      text: "when that creature dies",
    };
    board.game.delayedTriggers.push(delayedTrigger(trigger, [E("NOTHING")], 0, anthem.id, { remembered: [watched.id] }));
    destroy(board.game, other);
    expect(fired(board, "when that creature dies")).toBe(0);
    destroy(board.game, watched);
    const [noted] = pending(board);
    expect(noted?.ability.remembered).toEqual([watched.id]);
  });

  it("a state trigger fires once until its condition has been false (CR 603.8)", () => {
    const board = new Board();
    const text = "when you control no creatures";
    const condition: Condition = { $: "Condition", kind: ir("ConditionKind.CONTROLS_MATCHING") };
    watch(board, triggered(text, [], { is_state_trigger: true, intervening_if: condition }), "Glorious Anthem");
    let truth = true;
    installMatchHooks({ holds: () => truth });
    checkStateTriggers(board.game);
    checkStateTriggers(board.game);
    expect(fired(board, text)).toBe(1);
    truth = false;
    checkStateTriggers(board.game);
    truth = true;
    checkStateTriggers(board.game);
    expect(fired(board, text)).toBe(1);
  });
});

describe("putting triggers on the stack (CR 603.3)", () => {
  it("targets are chosen as it goes on the stack (CR 603.3d)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const anthem = board.play("Glorious Anthem");
    const ability = triggered("destroy target creature", [EventKind.END_STEP], {}, [E("DESTROY", { targets: ALL_CREATURES, is_targeted: true })]);
    board.game.pendingTriggers.push(pendingTrigger(anthem.id, ability, event(EventKind.END_STEP), 0));
    expect(putTriggersOnStack(board.game)).toBe(1);
    const top = board.game.objects.get(board.game.stack.at(-1)!)!;
    expect(top.source).toBe(anthem.id);
    expect(top.targets).toEqual([[bears.id]]);
    expect(board.game.pendingTriggers).toHaveLength(0);
  });

  it("an ability whose controller has left ceases to exist (CR 800.4a)", () => {
    const board = new Board();
    const anthem = board.play("Glorious Anthem", 1);
    player(board.game, 1).hasLost = true;
    board.game.pendingTriggers.push(pendingTrigger(anthem.id, triggered("gone", [EventKind.END_STEP]), event(EventKind.END_STEP), 1));
    expect(putTriggersOnStack(board.game)).toBe(0);
    expect(board.game.stack).toHaveLength(0);
  });

  it("a triggered mana ability resolves at once and never uses the stack (CR 605.3b)", () => {
    const board = new Board();
    const land = board.play("Forest");
    const ability = triggered("add {G}", [EventKind.TAPPED_FOR_MANA], {}, [E("ADD_MANA", { mana_produced: ["{G}"] })]);
    expect(isTriggeredManaAbility(board.game, land.id, ability)).toBe(true);
    board.game.pendingTriggers.push(pendingTrigger(land.id, ability, event(EventKind.TAPPED_FOR_MANA, { objectId: land.id }), 0));
    expect(putTriggersOnStack(board.game)).toBe(0);
    expect(board.game.stack).toHaveLength(0);
    expect(amountOfColor(player(board.game, 0).manaPool, Color.GREEN)).toBe(1);
    expect(land.zone).toBe(Zone.BATTLEFIELD);
  });
});

describe("executors that make triggers (CR 603.7, 603.9, 610.3)", () => {
  it("exile until this leaves: the card comes back when the exiler leaves (CR 610.3)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears", 1);
    const priest = board.play("Serra Angel");
    const r = newResolution(board.game, priest.id, 0, { targets: [[bears.id]] });
    execute(r, [E("EXILE", { targets: ALL_CREATURES, is_targeted: true, duration: Duration.WHILE_SOURCE_PERSISTS as never })]);
    const exiled = board.game.objects.get(bears.supersededBy)!;
    expect(exiled.zone).toBe(Zone.EXILE);
    expect(board.game.delayedTriggers).toHaveLength(1);
    destroy(board.game, priest);
    expect(putTriggersOnStack(board.game)).toBe(1);
    resolveTop(board.game);
    const back = board.game.objects.get(exiled.supersededBy)!;
    expect(back.zone).toBe(Zone.BATTLEFIELD);
    expect(back.controller).toBe(1);
  });

  it("an exile with no duration is permanent; another permanent leaving doesn't bring an 'until' exile back (CR 610.3)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const plain = board.play("Grizzly Bears", 1);
    const priest = board.play("Serra Angel");
    execute(newResolution(board.game, priest.id, 0, { targets: [[plain.id]] }), [E("EXILE", { targets: ALL_CREATURES, is_targeted: true })]);
    expect(board.game.delayedTriggers).toHaveLength(0);

    const held = board.play("Grizzly Bears", 1);
    const bystander = board.play("Grizzly Bears");
    execute(newResolution(board.game, priest.id, 0, { targets: [[held.id]] }), [
      E("EXILE", { targets: ALL_CREATURES, is_targeted: true, duration: Duration.WHILE_SOURCE_PERSISTS as never }),
    ]);
    destroy(board.game, bystander);
    expect(putTriggersOnStack(board.game)).toBe(0);
    expect(board.game.objects.get(held.supersededBy)!.zone).toBe(Zone.EXILE);
  });

  it("an 'until' exile whose source is already gone exiles nothing (CR 610.3a)", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const priest = board.play("Serra Angel");
    board.remove(priest);
    execute(newResolution(board.game, priest.id, 0, { targets: [[bears.id]] }), [
      E("EXILE", { targets: ALL_CREATURES, is_targeted: true, duration: Duration.WHILE_SOURCE_PERSISTS as never }),
    ]);
    expect(bears.zone).toBe(Zone.BATTLEFIELD);
    expect(board.game.delayedTriggers).toHaveLength(0);
  });

  it("a delayed trigger remembers what 'it' has become (CR 603.7c)", () => {
    const board = new Board();
    board.game.activePlayer = 0;
    const bears = board.play("Grizzly Bears");
    const anthem = board.play("Glorious Anthem");
    const r = newResolution(board.game, anthem.id, 0, { targets: [[bears.id]] });
    const later: TriggerCondition = { $: "TriggerCondition", event_kinds: { $set: [EventKind.END_STEP] } };
    execute(r, [E("EXILE", { targets: ALL_CREATURES, is_targeted: true }), E("DELAYED_TRIGGER", { trigger: later, children: [E("NOTHING")] })]);
    const [made] = board.game.delayedTriggers as { remembered: number[] }[];
    expect(made!.remembered).toEqual([bears.supersededBy]);
  });

  it("'when you do' triggers only if it was done (CR 603.9)", () => {
    const board = new Board();
    const anthem = board.play("Glorious Anthem");
    const reflexive = E("REFLEXIVE_TRIGGER", { children: [E("NOTHING")], text: "when you do" });
    const declined = newResolution(board.game, anthem.id, 0);
    declined.didIt = false;
    execute(declined, [reflexive]);
    expect(board.game.pendingTriggers).toHaveLength(0);
    execute(newResolution(board.game, anthem.id, 0), [reflexive]);
    expect(fired(board, "when you do")).toBe(1);
  });
});

describe("copy triggers of paid costs (CR 702.56a, 702.78a)", () => {
  it("replicate paid twice makes one trigger copying it twice", () => {
    const board = new Board();
    board.script("Glorious Anthem", {
      $: "Ability",
      kind: ir("AbilityKind.SPELL"),
      additional_cost: { $: "AdditionalCost", cost: { $: "Cost" }, optional: true, keyword: "Replicate" },
    });
    const spell = createObject(board.game, CARDS["Glorious Anthem"]!, 0, Zone.STACK);
    spell.additionalCostsPaid = [0, 0];
    putCastTriggers(board.game, spell);
    const [noted] = pending(board);
    expect(pending(board)).toHaveLength(1);
    expect(noted!.ability.effects![0]!.amount).toEqual({ $: "Value", constant: 2 });
    expect(noted!.ability.effects![0]!.targets!.specific).toEqual([spell.id]);
  });
});

describe("triggers that name what happened (CR 113.6k, 510.2)", () => {
  it("'when you cast this spell' triggers from the stack (CR 113.6k)", () => {
    const board = new Board();
    const text = "When you cast this spell, draw a card";
    board.script("Serra Angel", triggered(text, [EventKind.CAST_SPELL], { subject: THIS }));
    const spell = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.STACK);
    board.refresh();
    emit(board.game, event(EventKind.CAST_SPELL, { objectId: spell.id, player: 0 }));
    expect(fired(board, text)).toBe(1);
  });

  it("'deals combat damage to a player' fires for a player, not a blocker (CR 510.2)", () => {
    const board = new Board();
    const text = "Whenever this creature deals combat damage to a player";
    const me = watch(board, triggered(text, [EventKind.COMBAT_DAMAGE_DEALT], { source: THIS, to_player: true }));
    const blocker = board.play("Grizzly Bears", 1);
    board.game.pendingTriggers.length = 0;
    emit(board.game, event(EventKind.COMBAT_DAMAGE_DEALT, { player: 1, source: me.id, sourceController: 0, amount: 2 }));
    expect(fired(board, text)).toBe(1);
    emit(board.game, event(EventKind.COMBAT_DAMAGE_DEALT, { objectId: blocker.id, player: 1, source: me.id, sourceController: 0, amount: 2 }));
    expect(fired(board, text)).toBe(0);
    // Noncombat damage is a different event.
    emit(board.game, event(EventKind.DAMAGE_DEALT, { player: 1, source: me.id, sourceController: 0, amount: 2 }));
    expect(fired(board, text)).toBe(0);
  });
});
