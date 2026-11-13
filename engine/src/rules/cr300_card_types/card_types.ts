/**
 * Rules of particular card types. Ported from
 * mtgfish/rules/cr300_card_types/cr300_card_types.py, a piece at a time.
 */

import type { CardDef, FaceDef } from "../../kernel/card";
import { Layout, Zone } from "../../kernel/enums";
import { permanents, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { num } from "../ir_read";
import { addCounters } from "../cr100_game_concepts/actions";
import { hasSubtype, type Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";

const TRIGGERED = 2; // AbilityKind.TRIGGERED

/** Every chapter number printed on this object, in order (CR 714.2). */
export function chapterNumbers(chars: Characteristics): number[] {
  const chapters = new Set<number>();
  for (const a of chars.abilities) if (a.chapter && num(a.kind) === TRIGGERED) chapters.add(a.chapter);
  return [...chapters].sort((a, b) => a - b);
}

/**
 * CR 714.3b: as the precombat main phase begins, the active player puts a
 * lore counter on each Saga they control that has chapter abilities - so an
 * Urza's Saga under Blood Moon gets none.
 */
export function sagaLoreCounters(game: Game): void {
  for (const obj of permanents(game, game.activePlayer)) {
    const chars = characteristicsOf(game, obj);
    if (!hasSubtype(chars, "Saga") || chapterNumbers(chars).length === 0) continue;
    addCounters(game, obj, "lore", 1);
  }
}

// -- cast modes (Python cr300_card_types.CastMode and friends) ---------------------

/** Which set of characteristics an object uses (CR 715.4, 718.4, 720.4, 710). */
export const CastMode = { NORMAL: 0, ADVENTURE: 1, PROTOTYPE: 2, OMEN: 3, FLIPPED: 4 } as const;
export type CastMode = (typeof CastMode)[keyof typeof CastMode];

/** How a spell cast in each mode leaves the stack (CR 715.3d, 720.3d); null for the usual way. */
export const RESOLUTION_ZONE: ReadonlyMap<number, Zone | null> = new Map([
  [CastMode.NORMAL, null],
  [CastMode.ADVENTURE, Zone.EXILE],
  [CastMode.PROTOTYPE, null],
  [CastMode.OMEN, Zone.LIBRARY],
  [CastMode.FLIPPED, null],
]);

/** CR 702.160a: "Prototype [mana cost] - [power]/[toughness]" at the start of a line. */
const PROTOTYPE_LINE = /^Prototype\s+((?:\{[^}]+\})+)\s*[—-]\s*(\d+)\/(\d+)/m;

/** Whether a face prints a prototype line (Python `prototype_face(face) is not None`). */
export function hasPrototypeLine(face: FaceDef): boolean {
  return PROTOTYPE_LINE.test(face.oracle_text ?? "");
}

/** Which mode playing this face of this card means (cr300 `cast_mode_for_face`). */
export function castModeForFace(obj: GameObject, faceIndex: number): CastMode {
  if (faceIndex === 0) return CastMode.NORMAL;
  const card = obj.card as CardDef | null;
  const faces = card?.faces ?? [];
  // CR 718.3: the prototype face isn't printed in the card data; ask the layout first.
  if (card != null && num(card.layout) === Layout.PROTOTYPE) {
    const exists = faceIndex < faces.length || (faceIndex === 1 && faces.length > 0 && hasPrototypeLine(faces[0]!));
    return exists ? CastMode.PROTOTYPE : CastMode.NORMAL;
  }
  if (faceIndex >= faces.length) return CastMode.NORMAL;
  // CR 715.1, 720.1: by the subtype on that half, not the layout.
  const subtypes = faces[faceIndex]!.type_line.subtypes;
  if (subtypes.includes("Adventure")) return CastMode.ADVENTURE;
  if (subtypes.includes("Omen")) return CastMode.OMEN;
  return CastMode.NORMAL;
}
