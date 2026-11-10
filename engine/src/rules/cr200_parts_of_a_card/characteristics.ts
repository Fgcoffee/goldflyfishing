/**
 * The characteristics of an object at a moment in time (CR 109.3). Ported
 * from mtgfish/rules/cr200_parts_of_a_card/characteristics.py.
 *
 * Immutable: the layer system (CR 613) derives a new one at each step. Power
 * and toughness are null for a printed "*" - a signal for a characteristic-
 * defining ability (CR 208.2a), never zero.
 */

import { enumValue, irString, type Ability, type ManaCost } from "../../ir";
import { CardType, Color, Supertype } from "../../kernel/enums";
import { manaValue, ZERO_COST } from "../cr100_game_concepts/mana_value";
import { EMPTY_TYPE_LINE, typeLineText, type TypeLine } from "./typeline";

export interface Characteristics {
  readonly name: string;
  readonly manaCost: ManaCost;
  /** No mana cost at all (CR 202.1b) is different from {0}. */
  readonly hasManaCost: boolean;
  readonly colors: Color;
  readonly typeLine: TypeLine;
  readonly abilities: readonly Ability[];
  readonly power: number | null;
  readonly toughness: number | null;
  /** Base P/T after a setting effect (CR 208.4b), when one applied. */
  readonly basePowerAfterSetting: number | null;
  readonly baseToughnessAfterSetting: number | null;
  readonly loyalty: number | null;
  readonly defense: number | null;
  readonly handModifier: number;
  readonly lifeModifier: number;
  readonly text: string;
  /** CR 712.8c: a transformed permanent keeps its front face's mana value. */
  readonly manaValueFromFront: number | null;
}

export const EMPTY_CHARACTERISTICS: Characteristics = Object.freeze({
  name: "",
  manaCost: ZERO_COST,
  hasManaCost: false,
  colors: Color.NONE,
  typeLine: EMPTY_TYPE_LINE,
  abilities: Object.freeze([]) as readonly Ability[],
  power: null,
  toughness: null,
  basePowerAfterSetting: null,
  baseToughnessAfterSetting: null,
  loyalty: null,
  defense: null,
  handModifier: 0,
  lifeModifier: 0,
  text: "",
  manaValueFromFront: null,
});

export function characteristics(changes: Partial<Characteristics> = {}): Characteristics {
  return { ...EMPTY_CHARACTERISTICS, ...changes };
}

/** A modified copy: the layer system's only mutator. */
export function replaceCharacteristics(
  chars: Characteristics,
  changes: Partial<Characteristics>,
): Characteristics {
  return { ...chars, ...changes };
}

export function basePower(c: Characteristics): number | null {
  return c.basePowerAfterSetting ?? c.power;
}

export function baseToughness(c: Characteristics): number | null {
  return c.baseToughnessAfterSetting ?? c.toughness;
}

export function hasType(c: Characteristics, cardType: CardType): boolean {
  return (c.typeLine.types & cardType) !== 0;
}

export function hasSubtype(c: Characteristics, subtype: string): boolean {
  return c.typeLine.subtypes.includes(subtype);
}

export function hasSupertype(c: Characteristics, supertype: Supertype): boolean {
  return (c.typeLine.supertypes & supertype) !== 0;
}

export function isCreature(c: Characteristics): boolean {
  return (c.typeLine.types & CardType.CREATURE) !== 0;
}

export function isLand(c: Characteristics): boolean {
  return (c.typeLine.types & CardType.LAND) !== 0;
}

export function isLegendary(c: Characteristics): boolean {
  return (c.typeLine.supertypes & Supertype.LEGENDARY) !== 0;
}

/** CR 202.3. Callers on the stack add the chosen X themselves. */
export function charsManaValue(c: Characteristics): number {
  return c.manaValueFromFront ?? manaValue(c.manaCost);
}

export function abilityKeyword(ability: Ability): string {
  return ability.keyword === undefined ? "" : irString(ability.keyword);
}

/** Whether it has a keyword ability, case-insensitively. */
export function hasKeyword(c: Characteristics, keyword: string): boolean {
  const needle = keyword.toLowerCase();
  return c.abilities.some((a) => abilityKeyword(a).toLowerCase() === needle);
}

/** Every ability for a keyword (protection from two qualities, ward twice). */
export function keywordAbilities(c: Characteristics, keyword: string): Ability[] {
  const needle = keyword.toLowerCase();
  return c.abilities.filter((a) => abilityKeyword(a).toLowerCase() === needle);
}

export function keywords(c: Characteristics): string[] {
  return c.abilities.map(abilityKeyword).filter((k) => k.length > 0);
}

/** Abilities of one kind; `kind` is the AbilityKind number. */
export function abilitiesOf(c: Characteristics, kind: number): Ability[] {
  return c.abilities.filter((a) => enumValue(a.kind) === kind);
}

export function alternativeCosts(c: Characteristics): unknown[] {
  return c.abilities.map((a) => a.alternative_cost).filter((x) => x !== undefined && x !== null);
}

export function additionalCosts(c: Characteristics): unknown[] {
  return c.abilities.map((a) => a.additional_cost).filter((x) => x !== undefined && x !== null);
}

export function hasUnparsedAbilities(c: Characteristics): boolean {
  return c.abilities.some((a) => a.unparsed === true);
}

function show(value: number | null): string {
  return value === null ? "*" : String(value);
}

export function characteristicsText(c: Characteristics): string {
  const bits = [c.name || "(nameless)"];
  bits.push(typeLineText(c.typeLine));
  if (c.power !== null || c.toughness !== null) bits.push(`${show(c.power)}/${show(c.toughness)}`);
  if (c.loyalty !== null) bits.push(`[${c.loyalty}]`);
  if (c.defense !== null) bits.push(`<${c.defense}>`);
  return bits.join(" ");
}
