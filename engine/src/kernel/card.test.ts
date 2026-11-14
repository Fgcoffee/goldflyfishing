// Ported from FaceDef / CardDef in mtgfish/data/cards.py.
import { describe, expect, it } from "vitest";
import { castableFaces, isBasicLand, printedNumber, type CardDef, type FaceDef } from "./card";
import { CardType, Layout, Supertype } from "./enums";

function face(name: string, types: number, supertypes = 0): FaceDef {
  return {
    name,
    mana_cost: { $: "ManaCost", symbols: [] },
    has_mana_cost: false,
    type_line: { supertypes, types, subtypes: [] },
    oracle_text: "",
    power: null,
    toughness: null,
    loyalty: null,
    defense: null,
    colors: 0,
    color_indicator: null,
    keywords: [],
    produced_mana: [],
  };
}

function card(layout: number, faces: FaceDef[]): CardDef {
  return { oracle_id: "x", name: faces.map((f) => f.name).join(" // "), layout, faces, mana_value: 0, color_identity: 0, commander_legal: true };
}

describe("card data", () => {
  it("reads printed numbers, leaving * for a characteristic-defining ability (CR 208.2a)", () => {
    expect(printedNumber("2")).toBe(2);
    expect(printedNumber(" -1 ")).toBe(-1);
    expect(printedNumber("*")).toBeNull();
    expect(printedNumber("1+*")).toBeNull();
    expect(printedNumber("")).toBeNull();
    expect(printedNumber(null)).toBeNull();
  });

  it("offers both faces of a modal card, only the front of a transforming one", () => {
    const front = face("Front", CardType.SORCERY);
    const back = face("Back", CardType.LAND);
    expect(castableFaces(card(Layout.MODAL_DFC, [front, back]))).toHaveLength(2);
    expect(castableFaces(card(Layout.TRANSFORM, [front, back]))).toEqual([front]);
  });

  it("knows a basic land", () => {
    expect(isBasicLand(card(Layout.NORMAL, [face("Forest", CardType.LAND, Supertype.BASIC)]))).toBe(true);
    expect(isBasicLand(card(Layout.NORMAL, [face("Bayou", CardType.LAND)]))).toBe(false);
  });
});
