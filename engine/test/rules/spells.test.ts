// Ported from tests/rules/test_cr112_spells.py: what a spell is (CR 112.1),
// copies (CR 112.1a), owner and controller (CR 112.2), and effects that
// follow a permanent spell onto the battlefield (CR 112.3-4).
import { describe, expect, it } from "vitest";
import type { Effect } from "../../src/ir";
import { Color, Duration, Layer, Zone } from "../../src/kernel/enums";
import { createObject, type ContinuousEffect } from "../../src/kernel/game";
import { isPermanent, isSpell, newGameObject, ObjectKind, type GameObject } from "../../src/kernel/gameobject";
import { allocateObjectId, allocateTimestamp, NO_OBJECT } from "../../src/kernel/ids";
import { newResolution, registerContinuous } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { copySpell } from "../../src/rules/cr700_additional_rules/faces";
import { ir, numSet } from "../../src/rules/ir_read";
import { Board, CARDS } from "./board";

function board(): Board {
  const b = new Board();
  b.game.activePlayer = 0;
  return b;
}
const spell = (b: Board, name: string, controller = 0): GameObject => createObject(b.game, CARDS[name]!, controller, Zone.STACK);

function abilityOnTheStack(b: Board, source: GameObject): GameObject {
  const obj = newGameObject(allocateObjectId(b.game.ids), {
    kind: ObjectKind.ABILITY,
    owner: source.controller,
    controller: source.controller,
    zone: Zone.STACK,
    ability: { $: "Ability", kind: ir("AbilityKind.ACTIVATED"), text: "{T}: draw a card" },
    source: source.id,
    timestamp: allocateTimestamp(b.game.ids),
  });
  b.game.objects.set(obj.id, obj);
  b.game.stack.push(obj.id);
  return obj;
}

function continuous(b: Board, effect: Effect, layer: number): void {
  const entry: ContinuousEffect = {
    effect,
    source: NO_OBJECT,
    controller: 0,
    timestamp: allocateTimestamp(b.game.ids),
    layer,
    duration: Duration.PERMANENT,
    createdTurn: b.game.turn,
    ability: null,
    fromStaticAbility: false,
    isCda: false,
    expired: false,
  };
  b.game.continuousEffects.push(entry);
  b.refresh();
}

const paintWhite = (b: Board, obj: GameObject, zones: Zone[]): void =>
  continuous(
    b,
    { $: "Effect", kind: ir("EffectKind.SET_COLOR"), targets: { $: "ObjectFilter", specific: [obj.id], zones: { $set: zones } }, colors: Color.WHITE as never },
    Layer.COLOR,
  );

describe("what a spell is (CR 112.1)", () => {
  it("a card on the stack is; in hand or on the battlefield it isn't", () => {
    const b = board();
    expect(isSpell(spell(b, "Grizzly Bears"))).toBe(true);
    expect(isSpell(createObject(b.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND))).toBe(false);
    expect(isSpell(b.play("Grizzly Bears"))).toBe(false);
  });

  it("it stops being one when it resolves: the permanent is a new object (CR 608.3)", () => {
    const b = board();
    const cast = spell(b, "Grizzly Bears");
    resolveTop(b.game);
    const permanent = b.game.objects.get(cast.supersededBy)!;
    expect(isPermanent(permanent)).toBe(true);
    expect(isSpell(permanent)).toBe(false);
    expect(b.game.stack).toEqual([]);
  });

  it("an ability on the stack isn't a spell (CR 113.7)", () => {
    const b = board();
    const ability = abilityOnTheStack(b, b.play("Grizzly Bears"));
    expect(ability.zone).toBe(Zone.STACK);
    expect(isSpell(ability)).toBe(false);
  });
});

describe("copies (CR 112.1a, 112.2, 707.10)", () => {
  it("a copy of a spell is a spell; of an ability, not; only what is on the stack can be copied", () => {
    const b = board();
    const copy = copySpell(b.game, spell(b, "Grizzly Bears"));
    expect(copy?.kind).toBe(ObjectKind.COPY);
    expect(isSpell(copy!)).toBe(true);
    const abilityCopy = copySpell(b.game, abilityOnTheStack(b, b.play("Grizzly Bears")));
    expect(abilityCopy).not.toBeNull();
    expect(isSpell(abilityCopy!)).toBe(false);
    expect(copySpell(b.game, b.play("Grizzly Bears"))).toBeNull();
  });

  it("a spell's owner is its card's owner; a copy's is whoever put it on the stack", () => {
    const b = board();
    const original = spell(b, "Grizzly Bears", 1);
    expect([original.owner, original.controller]).toEqual([1, 1]);
    const copy = copySpell(b.game, original, { controller: 0 })!;
    expect([copy.owner, copy.controller]).toEqual([0, 0]);
    expect(original.owner).toBe(1);
  });
});

describe("a spell's characteristics (CR 112.3-4, 610.5)", () => {
  it("printed, as modified by an effect on the stack, abilities granted included", () => {
    const b = board();
    const cast = spell(b, "Grizzly Bears");
    expect(b.chars(cast).name).toBe("Grizzly Bears");
    expect(b.chars(cast).colors).toBe(Color.GREEN);
    paintWhite(b, cast, [Zone.STACK]);
    expect(b.chars(cast).colors).toBe(Color.WHITE);
    continuous(
      b,
      {
        $: "Effect",
        kind: ir("EffectKind.GRANT_ABILITY"),
        targets: { $: "ObjectFilter", specific: [cast.id], zones: { $set: [Zone.STACK] } },
        granted_abilities: [{ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Flying", text: "Flying" }],
      },
      Layer.ABILITY,
    );
    expect(b.keywords(cast).has("Flying")).toBe(true);
  });

  it("an effect on a permanent spell keeps applying to the permanent (CR 112.4)", () => {
    const b = board();
    const cast = spell(b, "Grizzly Bears");
    paintWhite(b, cast, [Zone.STACK, Zone.BATTLEFIELD]);
    resolveTop(b.game);
    expect(b.chars(b.game.objects.get(cast.supersededBy)!).colors).toBe(Color.WHITE);
  });

  it("a resolving spell's effect on a spell can reach the stack (CR 611.2c)", () => {
    const b = board();
    const target = spell(b, "Grizzly Bears");
    const effect: Effect = {
      $: "Effect",
      kind: ir("EffectKind.SET_COLOR"),
      targets: { $: "ObjectFilter", specific: [target.id], zones: { $set: [Zone.STACK] } },
      colors: Color.WHITE as never,
      is_targeted: true,
    };
    registerContinuous(newResolution(b.game, NO_OBJECT, 0, { targets: [[target.id]] }), effect);
    const registered = b.game.continuousEffects.at(-1)!.effect as Effect;
    expect(numSet(registered.targets!.zones).has(Zone.STACK)).toBe(true);
  });

  it("an effect on a permanent keeps applying to it", () => {
    const b = board();
    const bears = b.play("Grizzly Bears");
    paintWhite(b, bears, [Zone.BATTLEFIELD]);
    expect(b.chars(bears).colors).toBe(Color.WHITE);
  });
});

