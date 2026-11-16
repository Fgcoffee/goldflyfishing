// Dummy abilities (mtgfish/parser/dummies.py): a shape the parser recognises
// and the engine can't do yet. It never executes; it's logged as
// unimplemented and skipped, and a readable part beside it still works.
import "../../src/rules/match";
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { AbilityKindValues as AK, EffectKindValues as EK } from "../../src/ir/types";
import { SimpleAgent } from "../../src/ai/simple";
import { ActionKind, PASS, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { Phase, Step } from "../../src/kernel/enums";
import { newGame } from "../../src/kernel/game";
import { newPlayer } from "../../src/rules/cr100_game_concepts/player";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { isUsableManaAbility } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { RealBoard, REAL_ABILITIES } from "./real_cards";

describe("dummy abilities", () => {
  it("a DUMMY effect is logged as unimplemented and skipped", () => {
    const game = newGame([newPlayer(0), newPlayer(1)], { logEnabled: true, abilityProvider: REAL_ABILITIES });
    const before = game.players.map((p) => p.life);
    const dummy = { $: "Effect", kind: EK.DUMMY, rule: "untap-during-others-untap-step", text: "Untap all permanents you control during each other player's untap step." } as unknown as Effect;
    expect(() => execute(newResolution(game, 0, 0), [dummy])).not.toThrow();
    const logged = game.log.entries.filter((e) => e.kind === "unimplemented");
    expect(logged).toHaveLength(1);
    expect(logged[0]!.text).toContain("Untap all permanents");
    expect(game.players.map((p) => p.life)).toEqual(before);
  });

  it("Path of Ancestry still taps for mana: only its scry rider is a dummy", () => {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    const path = b.play("Path of Ancestry");
    const abilities = b.chars(path).abilities as readonly { is_mana_ability?: boolean; effects?: { kind?: number }[] }[];
    const mana = abilities.find((a) => a.is_mana_ability === true)!;
    expect(mana).toBeDefined();
    expect(mana.effects!.map((e) => e.kind)).toEqual([EK.ADD_MANA, EK.DUMMY]);
    // The engine treats it as a usable mana ability, as before the rider was read.
    expect(isUsableManaAbility(mana as never)).toBe(true);
  });

  it("a dummy on an activated ability keeps its cost; the bot doesn't pay for nothing", () => {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    const village = b.play("Mistrise Village");
    const abilities = b.chars(village).abilities as readonly { kind?: number; cost?: { components?: unknown[] }; effects?: { kind?: number }[] }[];
    const index = abilities.findIndex((a) => a.effects?.some((e) => e.kind === EK.DUMMY));
    const rider = abilities[index]!;
    expect(rider.kind).toBe(AK.ACTIVATED);
    expect(rider.cost?.components?.length).toBe(2); // {U}, {T}
    const activate = { kind: ActionKind.ACTIVATE_ABILITY, source: village.id, abilityIndex: index, targets: [], xValue: 0, faceIndex: 0, modeChoices: [], alternativeCost: -1, additionalCosts: [] } as unknown as Action;
    expect(new SimpleAgent({ lookahead: false }).chooseAction(b.game, 0, [activate])).toBe(PASS);
  });
});

