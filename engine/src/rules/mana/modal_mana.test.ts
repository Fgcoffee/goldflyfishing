/**
 * Modal mana abilities (CR 605.3b, 700.2). Ported from
 * tests/rules/test_cr605_modal_mana.py, through the rules session's
 * activateAbility and castSpell and the real mana host.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS, constant, effect } from "../../../test/rules/board";
import type { Ability, CostComponent, Effect } from "../../ir";
import { Color, Phase, Step, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { createObject } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { CostKind } from "../cr100_game_concepts/costs";
import { action, ActionKind } from "../cr100_game_concepts/priority";
import { activateAbility, castSpell } from "../cr600_spells_and_abilities/casting";
import { ir } from "../ir_read";
import { installMatch } from "../match";
import { amountOfColor, poolTotal } from "./pool";

const add = (symbol: string, colors: Color): Effect => effect({ kind: ir("EffectKind.ADD_MANA"), colors, mana_produced: [symbol], text: `Add ${symbol}` });
const chooseOne = (...modes: Effect[]): Effect => effect({ kind: ir("EffectKind.CHOOSE_MODE"), children: modes, amount: constant(1), text: "choose one" });
const modalManaAbility = (...modes: Effect[]): Ability => ({
  $: "Ability",
  kind: ir("AbilityKind.ACTIVATED"),
  effects: [chooseOne(...modes)],
  cost: { $: "Cost", components: [{ $: "CostComponent", kind: CostKind.TAP_SELF as never } as CostComponent] },
  is_mana_ability: true,
  text: "{T}: Add {R} or {G}.",
});

let board: Board;
const pool = () => board.game.players[0]!.manaPool;
const tap = (land: GameObject) => activateAbility(board.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: land.id, abilityIndex: 0 }));

beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
  board.game.phase = Phase.PRECOMBAT_MAIN;
  board.game.step = Step.MAIN;
});

describe("modal mana abilities (CR 605.3b, 700.2)", () => {
  it("adds mana without using the stack", () => {
    board.script("Forest", modalManaAbility(add("{R}", Color.RED), add("{G}", Color.GREEN)));
    const land = board.play("Forest");
    expect(tap(land)).toBeNull();
    expect(poolTotal(pool())).toBe(1);
    expect(amountOfColor(pool(), Color.RED)).toBe(1);
  });

  it("the controller chooses the mode, and only that mode runs (CR 700.2b)", () => {
    board.game.agents.set(0, { chooseModes: () => [1] });
    board.script("Forest", modalManaAbility(add("{R}", Color.RED), add("{G}", Color.GREEN)));
    tap(board.play("Forest"));
    expect(poolTotal(pool())).toBe(1);
    expect(amountOfColor(pool(), Color.GREEN)).toBe(1);
    expect(amountOfColor(pool(), Color.RED)).toBe(0);
  });

  it("tapped to pay a cost, it chooses modes like any activation (CR 601.2g, 602.2b)", () => {
    board.script("Forest", modalManaAbility(add("{G}", Color.GREEN), add("{R}", Color.RED)));
    board.play("Forest");
    board.play("Forest");
    const card = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND); // {1}{G}
    castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }));
    expect(board.game.stack.length).toBe(1);
  });

  it("a modal triggered mana ability adds mana too (CR 605.4)", () => {
    board.script(
      "Glorious Anthem",
      {
        $: "Ability",
        kind: ir("AbilityKind.TRIGGERED"),
        effects: [chooseOne(add("{W}", Color.WHITE), add("{U}", Color.BLUE))],
        trigger: { $: "TriggerCondition", event_kinds: { $set: [EventKind.MANA_ADDED] }, source: { $: "ObjectFilter", types_all: ir("CardType.LAND") }, text: "whenever a land produces mana" },
        text: "add one mana of a colour",
      } as Ability,
    );
    board.play("Glorious Anthem");
    board.script("Forest", modalManaAbility(add("{G}", Color.GREEN)));
    tap(board.play("Forest"));
    expect(poolTotal(pool())).toBe(2);
  });
});
