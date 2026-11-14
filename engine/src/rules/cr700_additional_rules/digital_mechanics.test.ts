/**
 * MTG Arena's perpetually, conjure and seek. Ported from
 * tests/rules/test_digital_mechanics.py (the cases this module answers on
 * its own; the zone-to-zone and layer cases need zones.ts and layers.ts to
 * call carryPerpetual and perpetualContinuousEffects).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, cardDef, constant, effect, filter, mana } from "../../../test/rules/board";
import type { CardDef } from "../../kernel/card";
import { CardType, Color, Supertype, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { cloneGame, createObject } from "../../kernel/game";
import { ObjectKind, type GameObject } from "../../kernel/gameobject";
import { ir } from "../ir_read";
import { installMatch } from "../match";
import {
  carryPerpetual,
  catalogCard,
  conjure,
  conjureDuplicate,
  conjuredName,
  named,
  perpetualChanges,
  perpetualContinuousEffects,
  perpetually,
  perpetuallyChanged,
  seek,
  setCardCatalog,
} from "./digital_mechanics";

const BEARS = cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, power: "2", toughness: "2", colors: Color.GREEN });
const BOLT = cardDef("Lightning Bolt", { cost: mana(0, Color.RED), types: CardType.INSTANT, colors: Color.RED });
const FOREST = cardDef("Forest", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Forest"] });
const pump = (n: number) => effect({ kind: ir("EffectKind.MODIFY_PT"), amount: constant(n), amount2: constant(n), targets: filter({}), is_targeted: true });

let board: Board;
const put = (card: CardDef, zone: Zone, owner = 0): GameObject => createObject(board.game, card, owner, zone);

beforeEach(() => {
  board = new Board();
  installMatch();
});

describe("perpetually", () => {
  it("binds a change to the card, aimed at the card itself, with fixed amounts", () => {
    const bears = put(BEARS, Zone.HAND);
    const change = perpetually(board.game, bears, pump(1), 0, { amounts: [3, 3] })!;
    expect(change.effect.targets).toBeNull();
    expect(change.effect.is_targeted).toBe(false);
    expect(change.effect.amount).toEqual({ $: "Value", constant: 3 });
    expect(perpetualChanges(board.game, bears)).toEqual([change]);
    expect(perpetuallyChanged(board.game)).toEqual([bears]);
  });

  it("only layered changes can be perpetual", () => {
    expect(perpetually(board.game, put(BEARS, Zone.HAND), effect({ kind: ir("EffectKind.DRAW") }), 0)).toBeNull();
  });

  it("carryPerpetual moves the changes to the new object (CR 400.7's exception)", () => {
    const bears = put(BEARS, Zone.HAND);
    perpetually(board.game, bears, pump(1), 0);
    const later = put(BEARS, Zone.GRAVEYARD);
    carryPerpetual(board.game, bears.id, later.id);
    expect(perpetualChanges(board.game, later).length).toBe(1);
  });

  it("becomes a continuous effect of the card it changes, with its own timestamp", () => {
    const bears = put(BEARS, Zone.BATTLEFIELD);
    const change = perpetually(board.game, bears, pump(1), 0)!;
    const [ce] = perpetualContinuousEffects(board.game, new Map([[bears.id, bears]]));
    expect(ce!.source).toBe(bears.id);
    expect(ce!.timestamp).toBe(change.timestamp);
  });

  it("a cloned game doesn't see changes made after the clone", () => {
    const bears = put(BEARS, Zone.HAND);
    const clone = cloneGame(board.game);
    perpetually(board.game, bears, pump(1), 0);
    expect(perpetualChanges(clone, bears)).toEqual([]);
  });
});

describe("conjure", () => {
  it("by name needs the catalogue, and does nothing without it", () => {
    expect(catalogCard(board.game, "Lightning Bolt")).toBeNull();
    setCardCatalog(board.game, { lookup: (name) => (name === "Lightning Bolt" ? BOLT : null) });
    expect(catalogCard(board.game, "Lightning Bolt")).toBe(BOLT);
    expect(catalogCard(board.game, "No Such Card")).toBeNull();
    expect(conjuredName(effect({ keywords: [named("Lightning Bolt")] }))).toBe("Lightning Bolt");
  });

  it("puts a new card, owned by the conjurer, into the named zone", () => {
    const card = conjure(board.game, 1, BOLT, Zone.HAND)!;
    expect(card.owner).toBe(1);
    expect(card.kind).toBe(ObjectKind.CARD);
    expect(board.game.players[1]!.hand).toEqual([card.id]);
  });

  it("onto the battlefield it enters, and ENTERS_BATTLEFIELD is announced", () => {
    const kinds: number[] = [];
    board.game.observer = (_g, e) => void kinds.push(e.kind);
    const card = conjure(board.game, 0, BEARS, Zone.BATTLEFIELD, { tapped: true })!;
    expect(board.game.battlefield).toContain(card.id);
    expect(card.tapped).toBe(true);
    expect(kinds).toContain(EventKind.ENTERS_BATTLEFIELD);
  });

  it("an instant conjured onto the battlefield isn't created (CR 304.4)", () => {
    expect(conjure(board.game, 0, BOLT, Zone.BATTLEFIELD)).toBeNull();
    expect(board.game.objects.size).toBe(0);
  });

  it("into the library at a position from the top", () => {
    for (let i = 0; i < 5; i++) put(FOREST, Zone.LIBRARY);
    const card = conjure(board.game, 0, BOLT, Zone.LIBRARY, { fromTop: 3 })!;
    expect(board.game.players[0]!.library.indexOf(card.id)).toBe(2);
  });

  it("a duplicate keeps perpetual changes; a card merely named the same doesn't", () => {
    const original = put(BEARS, Zone.HAND);
    perpetually(board.game, original, pump(1), 0);
    const duplicate = conjureDuplicate(board.game, 0, original, Zone.HAND, { keepPerpetual: true })!;
    const namesake = conjureDuplicate(board.game, 0, original, Zone.HAND, { keepPerpetual: false })!;
    expect(perpetualChanges(board.game, duplicate).length).toBe(1);
    expect(perpetualChanges(board.game, namesake).length).toBe(0);
  });
});

describe("seek", () => {
  it("takes a matching card from the library at random, without shuffling", () => {
    const lands = [put(FOREST, Zone.LIBRARY), put(BEARS, Zone.LIBRARY), put(FOREST, Zone.LIBRARY)];
    const before = [...board.game.players[0]!.library];
    const [found] = seek(board.game, 0, filter({ types_all: ir("CardType.LAND"), zones: { $set: [Zone.LIBRARY] } }), 1);
    expect(found!.zone).toBe(Zone.HAND);
    expect(board.chars(found!).name).toBe("Forest");
    // The rest keep their order: not a search, so no shuffle.
    const taken = lands.find((l) => l.supersededBy === found!.id)!;
    expect(board.game.players[0]!.library).toEqual(before.filter((id) => id !== taken.id));
  });

  it("with nothing to find, nothing happens", () => {
    put(BEARS, Zone.LIBRARY);
    expect(seek(board.game, 0, filter({ types_all: ir("CardType.LAND"), zones: { $set: [Zone.LIBRARY] } }), 1)).toEqual([]);
    expect(board.game.players[0]!.hand.length).toBe(0);
  });
});
