/**
 * Card data: the printed faces the engine builds characteristics from.
 * Ported from FaceDef and CardDef in mtgfish/data/cards.py.
 *
 * The card IR carries abilities only; this is the rest of a card. It follows
 * the IR's conventions - snake_case fields, numeric enums, an IR ManaCost -
 * so it can be exported from the Python CardDef 1:1 beside the IR.
 */

import type { ManaCost } from "../ir";
import { CardType, Color, DUAL_CASTABLE_LAYOUTS, Layout, Supertype } from "./enums";

/** A parsed type line, numeric (CR 205.1). */
export interface CardTypeLine {
  readonly supertypes: Supertype;
  readonly types: CardType;
  readonly subtypes: readonly string[];
}

export interface FaceDef {
  readonly name: string;
  readonly mana_cost: ManaCost;
  /** No mana cost (CR 202.1b) is different from {0}. */
  readonly has_mana_cost: boolean;
  readonly type_line: CardTypeLine;
  readonly oracle_text: string;
  /** As printed: "2", "*", "1+*", "-1"; null when absent. */
  readonly power: string | null;
  readonly toughness: string | null;
  readonly loyalty: string | null;
  readonly defense: string | null;
  readonly colors: Color;
  /** CR 204: when printed, it is the face's whole colour. */
  readonly color_indicator: Color | null;
  readonly keywords: readonly string[];
  readonly produced_mana: readonly string[];
}

export interface CardDef {
  readonly oracle_id: string;
  readonly name: string;
  readonly layout: Layout;
  readonly faces: readonly FaceDef[];
  readonly mana_value: number;
  readonly color_identity: Color;
  readonly commander_legal: boolean;
}

/**
 * A printed number, or null when it isn't a plain integer: "*", "1+*" and
 * "?" need a characteristic-defining ability (CR 208.2a), never zero.
 */
export function printedNumber(value: string | null): number | null {
  if (value === null) return null;
  const text = value.trim();
  return /^[+-]?\d+$/.test(text) ? Number.parseInt(text, 10) : null;
}

export function frontFace(card: CardDef): FaceDef {
  const face = card.faces[0];
  if (face === undefined) throw new RangeError(`${card.name} has no faces`);
  return face;
}

/**
 * Faces that can be cast or played from hand: split, modal double-faced and
 * Adventure cards offer a choice (CR 709.4, 712.2, 715.2); others the front.
 */
export function castableFaces(card: CardDef): readonly FaceDef[] {
  return DUAL_CASTABLE_LAYOUTS.has(card.layout) ? card.faces : [frontFace(card)];
}

export function isLegendaryCard(card: CardDef): boolean {
  return (frontFace(card).type_line.supertypes & Supertype.LEGENDARY) !== 0;
}

export function isBasicLand(card: CardDef): boolean {
  const front = frontFace(card).type_line;
  return (front.supertypes & Supertype.BASIC) !== 0 && (front.types & CardType.LAND) !== 0;
}
