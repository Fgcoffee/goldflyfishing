/**
 * A minimal test board for the rules tests: synthetic cards, scripted
 * abilities by card name (as Python's tests/rules/harness.py), and a small
 * stand-in for rules/match/ installed through kernel/hooks.ts. The stand-in
 * reads only the filter fields these tests use; the parser session's
 * rules/match/ replaces it.
 */

import type { Ability, Effect, ManaCost, ManaSymbol, ObjectFilter, Value } from "../../src/ir";
import type { CardDef, FaceDef } from "../../src/kernel/card";
import { CardType, Color, Layout, Supertype, Zone } from "../../src/kernel/enums";
import { createObject, invalidateAllCharacteristics, newGame, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { installMatchHooks } from "../../src/kernel/hooks";
import { NO_OBJECT } from "../../src/kernel/ids";
import { newPlayer } from "../../src/rules/cr100_game_concepts/player";
import { manaValue } from "../../src/rules/cr100_game_concepts/mana_value";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { abilityKeyword, charsManaValue, hasSubtype, type Characteristics } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { flag, ir, isEnum, list, num, numSet, strs } from "../../src/rules/ir_read";
import { installRegistry, SubtypeRegistry } from "../../src/rules/cr200_parts_of_a_card/typeline";

/** The subtype catalog the tests need, as the card database installs it. */
function installTestRegistry(): void {
  const reg = new SubtypeRegistry();
  reg.register(CardType.LAND, ["Plains", "Island", "Swamp", "Mountain", "Forest", "Urza's", "Mine"]);
  reg.register(CardType.CREATURE, ["Bear", "Angel", "Lhurgoyf", "Scarecrow", "Time Lord"]);
  reg.register(CardType.ENCHANTMENT, ["Aura"]);
  installRegistry(reg);
}

export function mana(generic: number, ...colored: Color[]): ManaCost {
  const symbols: ManaSymbol[] = [];
  if (generic > 0) symbols.push({ $: "ManaSymbol", kind: ir("ManaSymbolKind.GENERIC"), generic });
  const names: Record<number, string> = { 1: "WHITE", 2: "BLUE", 4: "BLACK", 8: "RED", 16: "GREEN" };
  for (const c of colored) symbols.push({ $: "ManaSymbol", kind: ir("ManaSymbolKind.COLORED"), colors: ir(`Color.${names[c]}`) });
  return { $: "ManaCost", symbols };
}

export function cardDef(
  name: string,
  fields: { cost?: ManaCost; types: number; supertypes?: number; subtypes?: string[]; power?: string; toughness?: string; colors?: Color },
): CardDef {
  const cost = fields.cost ?? { $: "ManaCost", symbols: [] };
  const face: FaceDef = {
    name,
    mana_cost: cost,
    has_mana_cost: fields.cost !== undefined,
    type_line: { supertypes: fields.supertypes ?? Supertype.NONE, types: fields.types, subtypes: fields.subtypes ?? [] },
    oracle_text: "",
    power: fields.power ?? null,
    toughness: fields.toughness ?? null,
    loyalty: null,
    defense: null,
    colors: fields.colors ?? Color.NONE,
    color_indicator: null,
    keywords: [],
    produced_mana: [],
  };
  return { oracle_id: name, name, layout: Layout.NORMAL, faces: [face], mana_value: manaValue(cost), color_identity: face.colors, commander_legal: true };
}

/** The cards the ported layer tests use, with their real costs and types. */
export const CARDS: Record<string, CardDef> = Object.fromEntries(
  [
    cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Bear"], power: "2", toughness: "2", colors: Color.GREEN }),
    cardDef("Serra Angel", { cost: mana(3, Color.WHITE, Color.WHITE), types: CardType.CREATURE, subtypes: ["Angel"], power: "4", toughness: "4", colors: Color.WHITE }),
    cardDef("Tarmogoyf", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Lhurgoyf"], power: "*", toughness: "1+*", colors: Color.GREEN }),
    cardDef("Glorious Anthem", { cost: mana(1, Color.WHITE, Color.WHITE), types: CardType.ENCHANTMENT, colors: Color.WHITE }),
    cardDef("Gaea's Anthem", { cost: mana(1, Color.GREEN, Color.GREEN), types: CardType.ENCHANTMENT, colors: Color.GREEN }),
    cardDef("Humility", { cost: mana(2, Color.WHITE, Color.WHITE), types: CardType.ENCHANTMENT, colors: Color.WHITE }),
    cardDef("Opalescence", { cost: mana(2, Color.WHITE, Color.WHITE), types: CardType.ENCHANTMENT, colors: Color.WHITE }),
    cardDef("Ghostly Prison", { cost: mana(2, Color.WHITE), types: CardType.ENCHANTMENT, colors: Color.WHITE }),
    cardDef("Levitation", { cost: mana(2, Color.BLUE, Color.BLUE), types: CardType.ENCHANTMENT, colors: Color.BLUE }),
    cardDef("Blood Moon", { cost: mana(2, Color.RED), types: CardType.ENCHANTMENT, colors: Color.RED }),
    cardDef("Painter's Servant", { cost: mana(2), types: CardType.ARTIFACT | CardType.CREATURE, subtypes: ["Scarecrow"], power: "1", toughness: "3" }),
    cardDef("Forest", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Forest"] }),
    cardDef("Island", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Island"] }),
    cardDef("Bayou", { types: CardType.LAND, subtypes: ["Swamp", "Forest"] }),
    cardDef("Urborg, Tomb of Yawgmoth", { types: CardType.LAND, supertypes: Supertype.LEGENDARY }),
  ].map((c) => [c.name, c]),
);

export class Board {
  readonly scripts = new Map<string, Ability[]>();
  generation = 0;
  readonly game: Game;

  constructor() {
    const scripts = this.scripts;
    const self = this;
    this.game = newGame([newPlayer(0), newPlayer(1)], {
      abilityProvider: {
        abilitiesFor: (card) => scripts.get((card as CardDef).name) ?? [],
        get generation() {
          return self.generation;
        },
      },
    });
    installTestMatcher();
    installTestRegistry();
  }

  script(name: string, ...abilities: Ability[]): void {
    this.scripts.set(name, abilities);
    this.generation += 1;
    this.refresh();
  }

  play(name: string, controller = 0): GameObject {
    const card = CARDS[name];
    if (card === undefined) throw new Error(`no test card ${name}`);
    const obj = createObject(this.game, card, controller, Zone.BATTLEFIELD);
    obj.summoningSick = false;
    this.refresh();
    return obj;
  }

  /** Take it off the battlefield (stands in for move_object, not ported yet). */
  remove(obj: GameObject): void {
    this.game.battlefield.splice(this.game.battlefield.indexOf(obj.id), 1);
    obj.zone = Zone.GRAVEYARD;
    obj.supersededBy = -1;
    this.refresh();
  }

  refresh(): void {
    invalidateAllCharacteristics(this.game);
  }

  chars(obj: GameObject): Characteristics {
    return characteristicsOf(this.game, obj);
  }

  pt(obj: GameObject): [number | null, number | null] {
    const c = this.chars(obj);
    return [c.power, c.toughness];
  }

  keywords(obj: GameObject): Set<string> {
    return new Set(this.chars(obj).abilities.map(abilityKeyword).filter((k) => k));
  }

  /** Colours this permanent's mana abilities make, as letters. */
  tapsFor(obj: GameObject): Set<string> {
    const letters: Record<number, string> = { 1: "W", 2: "U", 4: "B", 8: "R", 16: "G" };
    const out = new Set<string>();
    for (const a of this.chars(obj).abilities) {
      if (!flag(a.is_mana_ability)) continue;
      for (const e of list(a.effects)) {
        const colors = num(e.colors);
        for (const bit of [1, 2, 4, 8, 16]) if (colors & bit) out.add(letters[bit] as string);
      }
    }
    return out;
  }
}

/** A stand-in for rules/match/: only what the layer tests need. */
const NO_LIVE = NO_OBJECT;

export function installTestMatcher(): void {
  installMatchHooks({
    matches(game, obj, spec, context) {
      const chars = context.override ?? characteristicsOf(game, obj);
      return filterMatches(game, obj, chars, spec, context.source ?? 0, context.controller ?? -1);
    },
    find(game, spec, context) {
      return [...game.objects.values()].filter(
        (o) => o.supersededBy === NO_LIVE && filterMatches(game, o, characteristicsOf(game, o), spec, context.source ?? 0, context.controller ?? -1),
      );
    },
    evaluate(game, value, context) {
      return evaluate(game, value, context.source ?? 0);
    },
    holds: () => true,
    resolvePlayers(game, spec, context) {
      const you = context.controller ?? -1;
      const scope = num(spec.scope);
      const all = game.players.filter((p) => !p.hasLost).map((p) => p.id);
      if (scope === ir("PlayerScope.YOU")) return you >= 0 ? [you] : [];
      if (scope === ir("PlayerScope.EACH_PLAYER")) return all;
      return all.filter((p) => p !== you);
    },
    defendingPlayers: () => [],
    valueSubjects: (value: Value) => [flag(value.of_affected), false, false],
  });
}

function filterMatches(_game: Game, obj: GameObject, chars: Characteristics, spec: ObjectFilter, source: number, controller: number): boolean {
  const zones = numSet(spec.zones, [Zone.BATTLEFIELD]);
  if (zones.size > 0 && !zones.has(obj.zone)) return false;
  const typesAll = num(spec.types_all);
  if (typesAll && (chars.typeLine.types & typesAll) !== typesAll) return false;
  const supertypesNone = num(spec.supertypes_none);
  if (supertypesNone && (chars.typeLine.supertypes & supertypesNone) !== 0) return false;
  for (const s of strs(spec.subtypes_none)) if (hasSubtype(chars, s)) return false;
  if (flag(spec.other_than_source) && obj.id === source) return false;
  if (flag(spec.source_only) && obj.id !== source) return false;
  if (num(spec.controller) === 1 && obj.controller !== controller) return false;
  return true;
}

function evaluate(game: Game, value: Value, subject: number): number {
  if (isEnum(value.kind, "ValueKind.CONSTANT")) return value.constant ?? 0;
  if (isEnum(value.kind, "ValueKind.MANA_VALUE")) {
    const obj = game.objects.get(subject);
    return obj === undefined ? 0 : charsManaValue(characteristicsOf(game, obj));
  }
  throw new Error(`test evaluator: ${String(value.kind)}`);
}

// --- abilities transcribed from oracle text, as in tests/rules/test_layers.py

export const filter = (fields: Partial<ObjectFilter>): ObjectFilter => ({ $: "ObjectFilter", ...fields });
export const constant = (n: number): Value => ({ $: "Value", constant: n });
export const staticAbility = (text: string, ...effects: Effect[]): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), effects, text });
export const effect = (fields: Partial<Effect>): Effect => ({ $: "Effect", ...fields });

export const CREATURES_YOU_CONTROL = filter({ types_all: ir("CardType.CREATURE"), controller: ir("ControllerRelation.YOU") });
export const ALL_CREATURES = filter({ types_all: ir("CardType.CREATURE") });
