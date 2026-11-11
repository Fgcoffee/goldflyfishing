// Ported from tests/rules/test_layers.py: the layer system (CR 613).
import { beforeEach, describe, expect, it } from "vitest";
import type { Ability, Value } from "../../src/ir";
import { ir } from "../../src/rules/ir_read";
import { CardType, Color } from "../../src/kernel/enums";
import { addCounters } from "../../src/kernel/gameobject";
import { hasType } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { ALL_CREATURES, Board, constant, CREATURES_YOU_CONTROL, effect, filter, staticAbility } from "./board";

const gloriousAnthem = (): Ability =>
  staticAbility("Creatures you control get +1/+1.", effect({ kind: ir("EffectKind.MODIFY_PT"), targets: CREATURES_YOU_CONTROL, amount: constant(1), amount2: constant(1) }));
const humility = (): Ability =>
  staticAbility(
    "All creatures lose all abilities and have base power and toughness 1/1.",
    effect({ kind: ir("EffectKind.REMOVE_ABILITIES"), targets: ALL_CREATURES }),
    effect({ kind: ir("EffectKind.SET_PT"), targets: ALL_CREATURES, amount: constant(1), amount2: constant(1) }),
  );
const otherEnchantments = filter({ types_all: ir("CardType.ENCHANTMENT"), subtypes_none: ["Aura"], other_than_source: true });
const manaValueOfIt: Value = { $: "Value", kind: ir("ValueKind.MANA_VALUE"), of_affected: true };
const opalescence = (): Ability =>
  staticAbility(
    "Each other non-Aura enchantment is a creature ... equal to its mana value.",
    effect({ kind: ir("EffectKind.ADD_TYPE"), targets: otherEnchantments, types: ir("CardType.CREATURE") }),
    effect({ kind: ir("EffectKind.SET_PT"), targets: otherEnchantments, amount: manaValueOfIt, amount2: manaValueOfIt }),
  );
const bloodMoon = (): Ability =>
  staticAbility(
    "Nonbasic lands are Mountains.",
    effect({ kind: ir("EffectKind.SET_TYPE"), targets: filter({ types_all: ir("CardType.LAND"), supertypes_none: ir("Supertype.BASIC") }), types: ir("CardType.LAND"), keywords: ["Mountain"] }),
  );
const urborg = (): Ability =>
  staticAbility("Each land is a Swamp in addition to its other land types.", effect({ kind: ir("EffectKind.ADD_TYPE"), targets: filter({ types_all: ir("CardType.LAND") }), keywords: ["Swamp"] }));
const levitation = (): Ability =>
  staticAbility("Creatures you control have flying.", effect({ kind: ir("EffectKind.GRANT_ABILITY"), targets: CREATURES_YOU_CONTROL, keywords: ["Flying"] }));
const tarmogoyfCda = (): Ability => ({
  ...staticAbility("Base power and toughness 4/5 (CDA).", effect({ kind: ir("EffectKind.SET_PT"), amount: constant(4), amount2: constant(5) })),
  is_characteristic_defining: true,
});

function newBoard(): Board {
  const b = new Board();
  b.scripts.set("Glorious Anthem", [gloriousAnthem()]);
  b.scripts.set("Humility", [humility()]);
  b.scripts.set("Opalescence", [opalescence()]);
  b.scripts.set("Blood Moon", [bloodMoon()]);
  b.scripts.set("Urborg, Tomb of Yawgmoth", [urborg()]);
  b.scripts.set("Levitation", [levitation()]);
  b.scripts.set("Tarmogoyf", [tarmogoyfCda()]);
  return b;
}

let board: Board;
beforeEach(() => {
  board = newBoard();
});

describe("power and toughness layers (CR 613.4)", () => {
  it("printed power and toughness", () => {
    expect(board.pt(board.play("Grizzly Bears"))).toEqual([2, 2]);
  });

  it("a static pump applies, to its controller's creatures only", () => {
    const mine = board.play("Grizzly Bears", 0);
    const theirs = board.play("Grizzly Bears", 1);
    board.play("Glorious Anthem", 0);
    expect(board.pt(mine)).toEqual([3, 3]);
    expect(board.pt(theirs)).toEqual([2, 2]);
  });

  it("the effect stops when its source leaves (CR 611.3)", () => {
    const bears = board.play("Grizzly Bears");
    const anthem = board.play("Glorious Anthem");
    expect(board.pt(bears)).toEqual([3, 3]);
    board.remove(anthem);
    expect(board.pt(bears)).toEqual([2, 2]);
  });

  it("two anthems stack", () => {
    const bears = board.play("Grizzly Bears");
    board.play("Glorious Anthem");
    board.play("Gaea's Anthem");
    board.script("Gaea's Anthem", gloriousAnthem());
    expect(board.pt(bears)).toEqual([4, 4]);
  });

  it("counters apply after setting power and toughness (CR 613.4c)", () => {
    const bears = board.play("Grizzly Bears");
    addCounters(bears, "+1/+1", 3);
    board.play("Humility");
    expect(board.pt(bears)).toEqual([4, 4]);
  });

  it("setting beats modifying by layer, whatever the timestamps", () => {
    const a = board.play("Grizzly Bears");
    board.play("Glorious Anthem");
    board.play("Humility");
    expect(board.pt(a)).toEqual([2, 2]);
    const b = newBoard();
    const bears = b.play("Grizzly Bears");
    b.play("Humility");
    b.play("Glorious Anthem");
    expect(b.pt(bears)).toEqual([2, 2]);
  });

  it("+1/+1 and -1/-1 counters both count", () => {
    const bears = board.play("Grizzly Bears");
    addCounters(bears, "+1/+1", 2);
    addCounters(bears, "-1/-1", 1);
    expect(board.pt(bears)).toEqual([3, 3]);
  });

  it("a characteristic-defining ability applies in 7a (CR 613.4a)", () => {
    const goyf = board.play("Tarmogoyf");
    expect(board.pt(goyf)).toEqual([4, 5]);
    board.play("Glorious Anthem");
    expect(board.pt(goyf)).toEqual([5, 6]);
  });
});

describe("abilities (layer 6)", () => {
  it("Humility removes keywords", () => {
    const flier = board.play("Serra Angel");
    board.script("Serra Angel", { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Flying", text: "Flying" });
    expect(board.keywords(flier).has("Flying")).toBe(true);
    board.play("Humility");
    expect(board.keywords(flier).size).toBe(0);
  });

  it("a granted ability appears, and a later grant survives Humility", () => {
    const bears = board.play("Grizzly Bears");
    expect(board.keywords(bears).has("Flying")).toBe(false);
    board.play("Humility");
    board.play("Levitation");
    expect(board.keywords(bears).has("Flying")).toBe(true);
  });
});

describe("Opalescence (layers 4 and 7b)", () => {
  it("animates other enchantments at their own mana value", () => {
    board.play("Opalescence");
    const prison = board.play("Ghostly Prison");
    expect(hasType(board.chars(prison), CardType.CREATURE)).toBe(true);
    expect(board.pt(prison)).toEqual([3, 3]);
  });

  it("an animated anthem pumps itself", () => {
    board.play("Opalescence");
    expect(board.pt(board.play("Glorious Anthem"))).toEqual([4, 4]);
  });

  it("does not animate itself, and a noncreature has no P/T (CR 208.3)", () => {
    const opal = board.play("Opalescence");
    expect(hasType(board.chars(opal), CardType.CREATURE)).toBe(false);
    expect(board.pt(opal)).toEqual([null, null]);
  });

  for (const opalescenceFirst of [true, false]) {
    it(`Humility and Opalescence, ${opalescenceFirst ? "Opalescence" : "Humility"} first`, () => {
      const b = newBoard();
      const [opal, hum] = opalescenceFirst
        ? [b.play("Opalescence"), b.play("Humility")]
        : (() => {
            const h = b.play("Humility");
            return [b.play("Opalescence"), h];
          })();
      const prison = b.play("Ghostly Prison");
      const humilityChars = b.chars(hum);
      expect(hasType(humilityChars, CardType.CREATURE)).toBe(true);
      expect(humilityChars.abilities).toEqual([]);
      expect(b.pt(hum)).toEqual([4, 4]);
      expect(hasType(b.chars(opal), CardType.CREATURE)).toBe(false);
      expect(b.chars(opal).abilities.length).toBeGreaterThan(0);
      expect(b.pt(prison)).toEqual([3, 3]);
    });
  }
});

describe("land types (CR 305.6, 305.7, 613.8)", () => {
  it("basic land types carry their mana abilities", () => {
    expect(board.tapsFor(board.play("Forest"))).toEqual(new Set(["G"]));
    expect(board.tapsFor(board.play("Island"))).toEqual(new Set(["U"]));
    expect(board.tapsFor(board.play("Bayou"))).toEqual(new Set(["B", "G"]));
  });

  it("Urborg makes every land a Swamp", () => {
    const forest = board.play("Forest");
    board.play("Urborg, Tomb of Yawgmoth");
    expect(board.chars(forest).typeLine.subtypes).toContain("Swamp");
    expect(board.tapsFor(forest)).toEqual(new Set(["G", "B"]));
  });

  it("Blood Moon replaces nonbasic land types and spares basics", () => {
    const bayou = board.play("Bayou");
    const forest = board.play("Forest");
    board.play("Blood Moon");
    expect(board.chars(bayou).typeLine.subtypes).toEqual(["Mountain"]);
    expect(board.tapsFor(bayou)).toEqual(new Set(["R"]));
    expect(board.tapsFor(forest)).toEqual(new Set(["G"]));
  });

  for (const order of ["urborg-first", "moon-first"]) {
    it(`Blood Moon shuts off Urborg whatever the order (${order})`, () => {
      const b = newBoard();
      const bayou = b.play("Bayou");
      if (order === "urborg-first") {
        b.play("Urborg, Tomb of Yawgmoth");
        b.play("Blood Moon");
      } else {
        b.play("Blood Moon");
        b.play("Urborg, Tomb of Yawgmoth");
      }
      expect(b.chars(bayou).typeLine.subtypes).not.toContain("Swamp");
      expect(b.tapsFor(bayou)).toEqual(new Set(["R"]));
    });
  }
});

describe("colour and caching", () => {
  it("a colour-setting effect", () => {
    const bears = board.play("Grizzly Bears");
    expect(board.chars(bears).colors).toBe(Color.GREEN);
    board.scripts.set("Painter's Servant", [
      staticAbility("All cards are blue.", effect({ kind: ir("EffectKind.SET_COLOR"), targets: filter({ zones: { $set: [ir("Zone.BATTLEFIELD")] } }), colors: ir("Color.BLUE") })),
    ]);
    board.play("Painter's Servant");
    expect(board.chars(bears).colors).toBe(Color.BLUE);
  });

  it("repeated computation is stable, and the cache follows the board", () => {
    const bears = board.play("Grizzly Bears");
    expect(board.pt(bears)).toEqual([2, 2]);
    board.play("Glorious Anthem");
    expect(board.pt(bears)).toEqual([3, 3]);
    board.play("Humility");
    const first = board.pt(bears);
    for (let i = 0; i < 5; i += 1) {
      board.refresh();
      expect(board.pt(bears)).toEqual(first);
    }
  });
});
