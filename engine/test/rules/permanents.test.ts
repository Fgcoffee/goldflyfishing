// Ported from tests/rules/test_cr110_permanents.py: only the live object on
// the battlefield is a permanent (CR 110.1), a typeless permanent stays put
// (CR 110.4c), and a stolen permanent spell's permanent (CR 110.2b).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { CardType, Duration, Layer, Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { isLive, isPermanent, type GameObject } from "../../src/kernel/gameobject";
import { allocateTimestamp, NO_OBJECT } from "../../src/kernel/ids";
import { destroy, sacrifice, tap, untap } from "../../src/rules/cr100_game_concepts/actions";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { checkStateBasedActions } from "../../src/rules/cr700_additional_rules/sba";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, cardDef } from "./board";

CARDS["Typeless"] = cardDef("Typeless", { types: CardType.NONE });
CARDS["Jace"] = cardDef("Jace", { types: CardType.PLANESWALKER });

const graveyardNames = (b: Board, p: number): string[] => player(b.game, p).graveyard.map((id) => b.chars(b.game.objects.get(id)!).name);

/** It loses every card type (stands in for a type-changing effect). */
function loseTypes(b: Board, obj: GameObject): void {
  obj.card = CARDS["Typeless"];
  b.generation += 1;
  b.refresh();
}

describe("a permanent is the live object on the battlefield (CR 110.1)", () => {
  it("on the battlefield yes, in hand no, the husk a dead permanent left no", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const bears = b.play("Grizzly Bears");
    expect(isPermanent(bears)).toBe(true);
    expect(isPermanent(createObject(b.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND))).toBe(false);
    destroy(b.game, bears);
    expect(bears.zone).toBe(Zone.BATTLEFIELD);
    expect(isLive(bears)).toBe(false);
    expect(isPermanent(bears)).toBe(false);
  });

  it("destroying, sacrificing or tapping the husk does nothing", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const bears = b.play("Grizzly Bears");
    expect(destroy(b.game, bears)).toBe(true);
    expect(destroy(b.game, bears)).toBe(false);
    expect(sacrifice(b.game, bears)).toBe(false);
    expect(tap(b.game, bears)).toBe(false);
    expect(graveyardNames(b, 0)).toEqual(["Grizzly Bears"]);
    const live = b.play("Grizzly Bears");
    expect(tap(b.game, live)).toBe(true);
    expect(untap(b.game, live)).toBe(true);
  });
});

describe("a permanent with no permanent types (CR 110.4c)", () => {
  it("stays on the battlefield through SBAs, with no power or toughness (CR 208.3), and can still be destroyed", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const bears = b.play("Grizzly Bears");
    loseTypes(b, bears);
    expect(b.chars(bears).typeLine.types).toBe(CardType.NONE);
    expect(b.pt(bears)).toEqual([null, null]);
    checkStateBasedActions(b.game);
    expect(isPermanent(bears)).toBe(true);
    expect(destroy(b.game, bears)).toBe(true);
  });

  it("a typeless former planeswalker keeps its place; one with no loyalty still dies (CR 704.5i)", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const jace = b.play("Jace");
    const other = b.play("Jace");
    loseTypes(b, jace);
    checkStateBasedActions(b.game);
    expect(isPermanent(jace)).toBe(true);
    expect(isPermanent(other)).toBe(false);
  });
});

describe("a stolen permanent spell (CR 110.2b)", () => {
  function steal(b: Board, obj: GameObject, thief: number): void {
    const effect: Effect = {
      $: "Effect",
      kind: ir("EffectKind.GAIN_CONTROL"),
      targets: { $: "ObjectFilter", specific: [obj.id], zones: { $set: [Zone.STACK] } },
    };
    b.game.continuousEffects.push({
      effect,
      source: NO_OBJECT,
      controller: thief,
      timestamp: allocateTimestamp(b.game.ids),
      layer: Layer.CONTROL,
      duration: Duration.PERMANENT,
      createdTurn: b.game.turn,
      ability: null,
      fromStaticAbility: false,
      isCda: false,
      expired: false,
    });
    b.refresh();
    b.chars(obj);
  }

  it("the thief controls the permanent; by default it is still the caster's", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const spell = createObject(b.game, CARDS["Grizzly Bears"]!, 0, Zone.STACK);
    steal(b, spell, 1);
    expect(spell.controller).toBe(1);
    resolveTop(b.game);
    const permanent = b.game.objects.get(spell.supersededBy)!;
    expect(permanent.controller).toBe(1);
    expect(permanent.owner).toBe(0);
    expect(permanent.baseController).toBe(0);
  });

  it("unstolen, the two answers coincide", () => {
    const b = new Board();
    b.game.activePlayer = 0;
    const spell = createObject(b.game, CARDS["Grizzly Bears"]!, 0, Zone.STACK);
    resolveTop(b.game);
    const permanent = b.game.objects.get(spell.supersededBy)!;
    expect([permanent.controller, permanent.baseController]).toEqual([0, 0]);
  });
});
