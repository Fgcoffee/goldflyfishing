/**
 * The F61 guard against the real mana host (rules session's mana_host.ts).
 * Ported from tests/rules/test_mana_plan_stale_source.py; the "source has
 * left" case is in test/rules/paying.test.ts.
 */

import { describe, expect, it } from "vitest";
import { Board, cardDef, effect, mana } from "../../../test/rules/board";
import type { Ability, CostComponent } from "../../ir";
import { CardType, Supertype, Zone } from "../../kernel/enums";
import { createObject, invalidateAllCharacteristics } from "../../kernel/game";
import { CostKind } from "../cr100_game_concepts/costs";
import { destroy } from "../cr100_game_concepts/actions";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { CastError } from "../cr600_spells_and_abilities/cast_error";
import { executePlanFor, planPaymentFor } from "../cr600_spells_and_abilities/mana_host";
import { ir } from "../ir_read";
import { parseCost } from "./cost";
import { poolTotal } from "./pool";

const tap: CostComponent = { $: "CostComponent", kind: CostKind.TAP_SELF as never };
const tapFor = (...symbols: string[]): Ability => ({
  $: "Ability",
  kind: ir("AbilityKind.ACTIVATED"),
  is_mana_ability: true,
  cost: { $: "Cost", components: [tap] },
  effects: [effect({ kind: ir("EffectKind.ADD_MANA"), mana_produced: symbols })],
  text: `{T}: Add ${symbols.join("")}.`,
});

const SOL_RING = cardDef("Sol Ring", { cost: mana(1), types: CardType.ARTIFACT });
const FOREST = cardDef("Forest", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Forest"] });

function setUp() {
  const board = new Board();
  board.script("Sol Ring", tapFor("{C}", "{C}"));
  board.script("Forest", tapFor("{G}"));
  const ring = createObject(board.game, SOL_RING, 0, Zone.BATTLEFIELD);
  createObject(board.game, FOREST, 0, Zone.BATTLEFIELD);
  board.refresh();
  const plan = planPaymentFor(board.game, 0, parseCost("{3}"));
  expect(plan).not.toBeNull();
  expect(plan!.some((o) => o.source === ring.id)).toBe(true);
  return { board, ring, plan: plan! };
}

describe("a stale mana plan (F61, CR 605.3b, 601.2h)", () => {
  it("a planned source that was destroyed rewinds the cast", () => {
    const { board, ring, plan } = setUp();
    destroy(board.game, ring);
    expect(() => executePlanFor(board.game, 0, plan)).toThrow(CastError);
  });

  it("a planned source that lost its ability rewinds the cast", () => {
    const { board, ring, plan } = setUp();
    board.game.abilityProvider = { abilitiesFor: () => [], generation: 99 };
    invalidateAllCharacteristics(board.game);
    expect(characteristicsOf(board.game, ring).abilities.length).toBe(0);
    expect(() => executePlanFor(board.game, 0, plan)).toThrow(CastError);
  });

  it("an untouched plan still pays", () => {
    const { board, plan } = setUp();
    executePlanFor(board.game, 0, plan);
    expect(poolTotal(board.game.players[0]!.manaPool)).toBe(3);
  });
});
