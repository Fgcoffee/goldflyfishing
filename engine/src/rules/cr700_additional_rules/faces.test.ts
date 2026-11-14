/**
 * Transforming and copying (CR 707, 712). Ported from tests/rules/
 * test_faces.py (transform and copy cases) and test_cr707_copy_new_targets.py.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, cardDef, constant, CREATURES_YOU_CONTROL, effect, filter, mana, staticAbility } from "../../../test/rules/board";
import type { Ability } from "../../ir";
import type { CardDef } from "../../kernel/card";
import { CardType, Color, Layout, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { createObject } from "../../kernel/game";
import { addCounters, type GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { ir } from "../ir_read";
import { installMatch } from "../match";
import { moveObject } from "../zones";
import { canEnterTransformed, canTransform, copyPermanent, copySpell, transform } from "./faces";
import { checkStateBasedActions } from "./sba";

function dfc(name: string, layout: number, front: CardDef, back: CardDef): CardDef {
  return { ...front, name, layout, faces: [front.faces[0]!, back.faces[0]!] };
}

const DELVER = dfc(
  "Delver of Secrets // Insectile Aberration",
  Layout.TRANSFORM,
  cardDef("Delver of Secrets", { cost: mana(0, Color.BLUE), types: CardType.CREATURE, subtypes: ["Human", "Wizard"], power: "1", toughness: "1", colors: Color.BLUE }),
  cardDef("Insectile Aberration", { types: CardType.CREATURE, subtypes: ["Human", "Insect"], power: "3", toughness: "2", colors: Color.BLUE }),
);
const BALA_GED = dfc(
  "Bala Ged Recovery // Bala Ged Sanctuary",
  Layout.MODAL_DFC,
  cardDef("Bala Ged Recovery", { cost: mana(2, Color.GREEN), types: CardType.SORCERY, colors: Color.GREEN }),
  cardDef("Bala Ged Sanctuary", { types: CardType.LAND }),
);
const CARDS: Record<string, CardDef> = Object.fromEntries(
  [
    DELVER,
    BALA_GED,
    cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Bear"], power: "2", toughness: "2", colors: Color.GREEN }),
    cardDef("Hill Giant", { cost: mana(3, Color.RED), types: CardType.CREATURE, subtypes: ["Giant"], power: "3", toughness: "3", colors: Color.RED }),
    cardDef("Serra Angel", { cost: mana(3, Color.WHITE, Color.WHITE), types: CardType.CREATURE, subtypes: ["Angel"], power: "4", toughness: "4", colors: Color.WHITE }),
    cardDef("Glorious Anthem", { cost: mana(1, Color.WHITE, Color.WHITE), types: CardType.ENCHANTMENT, colors: Color.WHITE }),
    cardDef("Shock", { cost: mana(0, Color.RED), types: CardType.INSTANT, colors: Color.RED }),
  ].map((c) => [c.name, c]),
);

let board: Board;
function put(name: string, zone: Zone = Zone.BATTLEFIELD, controller: PlayerId = 0): GameObject {
  const obj = createObject(board.game, CARDS[name]!, controller, zone);
  obj.summoningSick = false;
  board.refresh();
  return obj;
}
const nameOf = (obj: GameObject) => board.chars(obj).name;

beforeEach(() => {
  board = new Board();
  installMatch();
});

describe("transforming (CR 712.9, 712.18)", () => {
  it("a transforming card can transform, and swaps its face", () => {
    const delver = put("Delver of Secrets // Insectile Aberration");
    expect(canTransform(board.game, delver)).toBe(true);
    expect(nameOf(delver)).toBe("Delver of Secrets");
    expect(transform(board.game, delver)).toBe(true);
    expect(nameOf(delver)).toBe("Insectile Aberration");
    expect(board.pt(delver)).toEqual([3, 2]);
  });

  it("keeps the same object, with its counters and damage (CR 712.18)", () => {
    const delver = put("Delver of Secrets // Insectile Aberration");
    const id = delver.id;
    addCounters(delver, "+1/+1", 2);
    delver.damage = 1;
    transform(board.game, delver);
    expect(delver.id).toBe(id);
    expect(board.game.battlefield).toContain(id);
    expect(delver.counters.get("+1/+1")).toBe(2);
    expect(delver.damage).toBe(1);
    expect(board.pt(delver)).toEqual([5, 4]);
  });

  it("emits TRANSFORMED", () => {
    const kinds: number[] = [];
    board.game.observer = (_g, e) => {
      kinds.push(e.kind);
    };
    transform(board.game, put("Delver of Secrets // Insectile Aberration"));
    expect(kinds).toContain(EventKind.TRANSFORMED);
  });

  it("transforms back again", () => {
    const delver = put("Delver of Secrets // Insectile Aberration");
    transform(board.game, delver);
    transform(board.game, delver);
    expect(nameOf(delver)).toBe("Delver of Secrets");
  });

  it("a single-faced card can't transform", () => {
    const bears = put("Grizzly Bears");
    expect(canTransform(board.game, bears)).toBe(false);
    expect(transform(board.game, bears)).toBe(false);
  });

  it("a card in hand can't transform", () => {
    expect(canTransform(board.game, put("Delver of Secrets // Insectile Aberration", Zone.HAND))).toBe(false);
  });

  it("an MDFC whose other face is a sorcery doesn't transform (CR 712.10)", () => {
    const card = put("Bala Ged Recovery // Bala Ged Sanctuary");
    card.faceIndex = 1;
    board.refresh();
    expect(nameOf(card)).toBe("Bala Ged Sanctuary");
    expect(canTransform(board.game, card)).toBe(false);
    expect(transform(board.game, card)).toBe(false);
  });

  it("can enter transformed only with a permanent back face (CR 712.14a)", () => {
    expect(canEnterTransformed(put("Delver of Secrets // Insectile Aberration", Zone.HAND))).toBe(true);
    expect(canEnterTransformed(put("Grizzly Bears", Zone.HAND))).toBe(false);
  });
});

describe("copying permanents (CR 707.2, 707.3)", () => {
  it("a copy takes the original's characteristics", () => {
    const original = put("Serra Angel", Zone.BATTLEFIELD, 1);
    const copier = put("Grizzly Bears");
    copyPermanent(board.game, copier, original);
    expect(nameOf(copier)).toBe("Serra Angel");
    expect(board.pt(copier)).toEqual([4, 4]);
  });

  it("a copy doesn't take counters", () => {
    const original = put("Grizzly Bears", Zone.BATTLEFIELD, 1);
    addCounters(original, "+1/+1", 3);
    board.refresh();
    expect(board.pt(original)).toEqual([5, 5]);
    const copier = put("Hill Giant");
    copyPermanent(board.game, copier, original);
    expect(board.pt(copier)).toEqual([2, 2]);
  });

  it("a copy doesn't take a pump effect (CR 613.2)", () => {
    board.script(
      "Glorious Anthem",
      staticAbility("Creatures you control get +1/+1.", effect({ kind: ir("EffectKind.MODIFY_PT"), targets: CREATURES_YOU_CONTROL, amount: constant(1), amount2: constant(1) })),
    );
    const original = put("Grizzly Bears", Zone.BATTLEFIELD, 1);
    put("Glorious Anthem", Zone.BATTLEFIELD, 1);
    expect(board.pt(original)).toEqual([3, 3]);
    const copier = put("Hill Giant");
    copyPermanent(board.game, copier, original);
    expect(board.pt(copier)).toEqual([2, 2]);
  });

  it("the copy keeps its own counters", () => {
    const original = put("Grizzly Bears", Zone.BATTLEFIELD, 1);
    const copier = put("Hill Giant");
    addCounters(copier, "+1/+1", 2);
    copyPermanent(board.game, copier, original);
    expect(board.pt(copier)).toEqual([4, 4]);
  });

  it("copying a transformed permanent uses the face that's up (CR 707.8)", () => {
    const delver = put("Delver of Secrets // Insectile Aberration", Zone.BATTLEFIELD, 1);
    transform(board.game, delver);
    const copier = put("Grizzly Bears");
    copyPermanent(board.game, copier, delver);
    expect(nameOf(copier)).toBe("Insectile Aberration");
  });

  it("a later copy effect wins (CR 613.7)", () => {
    const first = put("Serra Angel", Zone.BATTLEFIELD, 1);
    const second = put("Hill Giant", Zone.BATTLEFIELD, 1);
    const copier = put("Grizzly Bears");
    copyPermanent(board.game, copier, first);
    copyPermanent(board.game, copier, second);
    expect(nameOf(copier)).toBe("Hill Giant");
  });
});

describe("copying spells (CR 707.10)", () => {
  const SHOCK: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.SPELL"),
    effects: [
      effect({
        kind: ir("EffectKind.DAMAGE"),
        targets: filter({ types_all: ir("CardType.CREATURE") }),
        is_targeted: true,
        amount: constant(2),
      }),
    ],
    text: "Shock deals 2 damage to target creature.",
  };

  function onStack(name: string, controller: PlayerId = 0): GameObject {
    const spell = put(name, Zone.STACK, controller);
    spell.wasCast = true;
    return spell;
  }

  it("puts a copy on the stack, owned by its controller (CR 112.2)", () => {
    const spell = onStack("Grizzly Bears", 0);
    const copy = copySpell(board.game, spell, { controller: 1 })!;
    expect(copy).not.toBeNull();
    expect(board.game.stack.length).toBe(2);
    expect(copy.zone).toBe(Zone.STACK);
    expect(copy.owner).toBe(1);
    expect(copy.controller).toBe(1);
  });

  it("the copy was not cast, and COPIED is emitted", () => {
    const kinds: number[] = [];
    board.game.observer = (_g, e) => {
      kinds.push(e.kind);
    };
    const copy = copySpell(board.game, onStack("Grizzly Bears"))!;
    expect(copy.wasCast).toBe(false);
    expect(kinds).toContain(EventKind.COPIED);
    expect(kinds).not.toContain(EventKind.CAST_SPELL);
  });

  it("the copy takes the original's choices (CR 707.2)", () => {
    const spell = onStack("Grizzly Bears");
    spell.xValue = 3;
    spell.targets = [[42]];
    spell.chosenModes = [1];
    const copy = copySpell(board.game, spell)!;
    expect(copy.xValue).toBe(3);
    expect(copy.targets).toEqual([[42]]);
    expect(copy.chosenModes).toEqual([1]);
    copy.targets[0]!.push(7);
    expect(spell.targets).toEqual([[42]]); // its own copy of the choices
  });

  it("only a spell on the stack can be copied", () => {
    expect(copySpell(board.game, put("Grizzly Bears"))).toBeNull();
  });

  it("a copy off the stack ceases to exist (CR 704.5e)", () => {
    const copy = copySpell(board.game, onStack("Grizzly Bears"))!;
    moveObject(board.game, copy, Zone.GRAVEYARD, { toPlayer: 0 });
    checkStateBasedActions(board.game);
    expect(board.game.stack.length).toBe(1);
    expect(board.game.players[0]!.graveyard.length).toBe(0);
  });

  describe("new targets (CR 707.10c, test_cr707_copy_new_targets.py)", () => {
    let bears: GameObject;
    let giant: GameObject;
    let spell: GameObject;
    beforeEach(() => {
      board.script("Shock", SHOCK);
      bears = put("Grizzly Bears", Zone.BATTLEFIELD, 1);
      giant = put("Hill Giant", Zone.BATTLEFIELD, 1);
      spell = onStack("Shock");
      spell.targets = [[bears.id]];
    });

    it("the copy may be aimed elsewhere", () => {
      board.game.agents.set(0, { chooseNewTargets: () => [[giant.id]] });
      const copy = copySpell(board.game, spell, { mayChooseNewTargets: true })!;
      expect(copy.targets).toEqual([[giant.id]]);
      expect(spell.targets).toEqual([[bears.id]]);
    });

    it("without a new choice the copy keeps the targets", () => {
      const copy = copySpell(board.game, spell, { mayChooseNewTargets: true })!;
      expect(copy.targets).toEqual([[bears.id]]);
    });

    it("a choice that isn't legal isn't taken", () => {
      const enchantment = put("Glorious Anthem", Zone.BATTLEFIELD, 1);
      board.game.agents.set(0, { chooseNewTargets: () => [[enchantment.id]] });
      const copy = copySpell(board.game, spell, { mayChooseNewTargets: true })!;
      expect(copy.targets[0]).not.toContain(enchantment.id);
      expect(copy.targets[0]!.length).toBe(1); // a required target is still filled
    });

    it("a copy whose text doesn't say so keeps the targets", () => {
      board.game.agents.set(0, { chooseNewTargets: () => [[giant.id]] });
      const copy = copySpell(board.game, spell)!;
      expect(copy.targets).toEqual([[bears.id]]);
    });
  });
});
