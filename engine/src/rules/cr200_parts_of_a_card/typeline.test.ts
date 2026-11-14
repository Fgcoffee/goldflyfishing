// Ported from the behaviour documented in
// mtgfish/rules/cr200_parts_of_a_card/cr205_typeline.py.
import { describe, expect, it } from "vitest";
import { CardType, Supertype } from "../../kernel/enums";
import {
  addingTypes,
  creatureTypesOf,
  isPermanentType,
  parseTypeLine,
  removingTypes,
  scanTypeLine,
  SubtypeRegistry,
  typeLineText,
} from "./typeline";

describe("type lines (CR 205)", () => {
  it("parses supertypes, types and subtypes", () => {
    const line = parseTypeLine("Legendary Creature — Human Wizard");
    expect(line.supertypes).toBe(Supertype.LEGENDARY);
    expect(line.types).toBe(CardType.CREATURE);
    expect(line.subtypes).toEqual(["Human", "Wizard"]);
    expect(typeLineText(line)).toBe("Legendary Creature — Human Wizard");
  });

  it("splits multi-word subtypes by longest match", () => {
    expect(parseTypeLine("Creature - Time Lord Alien").subtypes).toEqual(["Time Lord", "Alien"]);
    expect(parseTypeLine("Land — Urza's Mine").subtypes).toEqual(["Urza's", "Mine"]);
  });

  it("is strict about unknown type words unless asked not to be", () => {
    expect(() => parseTypeLine("Scariest Creature You'll Ever See")).toThrow();
    const { line, unknown } = scanTypeLine("Scariest Creature You'll Ever See");
    expect(line.types).toBe(CardType.CREATURE);
    expect(unknown).toEqual(["Scariest", "You'll", "Ever", "See"]);
    expect(parseTypeLine("Tribal Instant — Goblin").types).toBe(CardType.KINDRED | CardType.INSTANT);
  });

  it("answers permanent and creature-type questions from a registry", () => {
    const reg = new SubtypeRegistry();
    reg.register(CardType.CREATURE, ["Goblin", "Time Lord"]);
    reg.register(CardType.LAND, ["Forest"]);
    const line = parseTypeLine("Kindred Artifact — Goblin Equipment", { registry: reg });
    expect(creatureTypesOf(line, reg)).toEqual(["Goblin"]);
    expect(isPermanentType(line)).toBe(true);
    expect(isPermanentType(parseTypeLine("Sorcery"))).toBe(false);
    expect(reg.typesFor("Forest")).toBe(CardType.LAND);
    expect(reg.known("Elf")).toBe(false);
  });

  it("adds and removes types without disturbing the rest (CR 205.1b, 613.1d)", () => {
    const bears = parseTypeLine("Creature — Bear");
    const added = addingTypes(bears, { types: CardType.ARTIFACT, subtypes: ["Bear", "Golem"] });
    expect(added.types).toBe(CardType.CREATURE | CardType.ARTIFACT);
    expect(added.subtypes).toEqual(["Bear", "Golem"]);
    const removed = removingTypes(added, { types: CardType.CREATURE, subtypes: ["Bear"] });
    expect(removed.types).toBe(CardType.ARTIFACT);
    expect(removed.subtypes).toEqual(["Golem"]);
    expect(bears.subtypes).toEqual(["Bear"]);
  });
});
