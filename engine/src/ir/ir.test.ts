/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import { check, complete, enumValue, IR_VERSION, readCard } from "./index";
import type { Ability, CardDocument, Effect, ObjectFilter, Value } from "./index";

// The bench decks' cards, exported by `python -m mtgfish.tools.ir_export samples`.
const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });

function samples(): CardDocument[] {
  return Object.entries(FILES)
    .filter(([path]) => !path.endsWith("/decks.json"))
    .map(([, document]) => readCard(document));
}

function decksFile(): unknown {
  const entry = Object.entries(FILES).find(([path]) => path.endsWith("/decks.json"));
  if (entry === undefined) throw new Error("schema/samples/decks.json is missing");
  return entry[1];
}

function byName(name: string): CardDocument {
  const card = samples().find((c) => c.name === name);
  if (card === undefined) throw new Error(`${name} is not in the samples`);
  return card;
}

describe("card IR", () => {
  it("reads every bench-deck sample as IR version 1", () => {
    const cards = samples();
    expect(cards.length).toBeGreaterThan(200);
    for (const card of cards) {
      expect(card.ir_version).toBe(IR_VERSION);
      expect(check(card.faces), card.name).toEqual([]);
    }
  });

  it("names every deck card in decks.json", () => {
    const decks = decksFile() as Record<
      string,
      { cards: { oracle_id: string }[]; unresolved: string[] }
    >;
    const have = new Set(samples().map((c) => c.oracle_id));
    for (const [deck, list] of Object.entries(decks)) {
      expect(list.unresolved, deck).toEqual([]);
      for (const entry of list.cards) expect(have.has(entry.oracle_id), deck).toBe(true);
    }
  });

  it("turns enum literals into the Python engine's numbers", () => {
    expect(enumValue("CardType.ARTIFACT|CREATURE")).toBe(enumValue("CardType.ARTIFACT") | enumValue("CardType.CREATURE"));
    expect(enumValue("CardType.NONE")).toBe(0);
    expect(() => enumValue("Zone.NOWHERE")).toThrow();
  });

  it("fills in the defaults the JSON leaves out", () => {
    const [face] = byName("Sol Ring").faces;
    if (face === undefined) throw new Error("no face");
    const [ability] = complete(face.abilities) as Ability[];
    if (ability === undefined) throw new Error("no ability");
    // Every field is present after `complete`, defaults included.
    expect(ability.unparsed).toBe(false);
    expect(ability.is_mana_ability).toBe(true);
    const [effect] = ability.effects as Effect[];
    expect(effect?.kind).toBe("EffectKind.ADD_MANA");
    const targets = (effect?.targets ?? null) as ObjectFilter | null;
    expect(targets === null || targets.zones !== undefined).toBe(true);
  });

  it("carries the numbers a card prints", () => {
    const [face] = byName("Lightning Bolt").faces;
    const [ability] = complete(face?.abilities ?? []) as Ability[];
    const [damage] = (ability?.effects ?? []) as Effect[];
    expect(damage?.kind).toBe("EffectKind.DAMAGE");
    expect((damage?.amount as Value).constant).toBe(3);
    expect(damage?.is_targeted).toBe(true);
  });

  it("refuses what is not IR", () => {
    expect(check({ $: "NoSuchClass" })).not.toEqual([]);
    expect(check({ $: "ObjectFilter", no_such_field: 1 })).not.toEqual([]);
    expect(() => readCard({ ir_version: IR_VERSION + 1, faces: [] })).toThrow();
  });
});
