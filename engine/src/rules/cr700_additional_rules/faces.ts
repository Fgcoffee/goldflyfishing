/**
 * Faces of multi-faced cards (CR 709, 712, 715, 718). Ported from
 * mtgfish/rules/cr700_additional_rules/cr707_faces.py: only the two
 * functions legal actions needs so far (castable_face_indices,
 * playable_land_face_indices) and layout_of, written by the rules session.
 * The rest of cr707_faces.py belongs to the parser session's "Copies, faces
 * and tokens" item, which owns this file from here on.
 */

import type { CardDef } from "../../kernel/card";
import { CardType, Layout } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { NO_OBJECT } from "../../kernel/ids";
import { hasPrototypeLine } from "../cr300_card_types/card_types";

export function layoutOf(obj: GameObject): number {
  return (obj.card as CardDef | null)?.layout ?? Layout.NORMAL;
}

const facesOf = (obj: GameObject) => (obj.card as CardDef | null)?.faces ?? [];

/** Faces of a card that may be cast (CR 712.11, 712.12, 709, 715, 718.3, 722.3). */
export function castableFaceIndices(_game: Game, obj: GameObject): number[] {
  const faces = facesOf(obj);
  if (faces.length === 0) return [0];
  if (obj.prepareCopyOf !== NO_OBJECT) return [1]; // CR 722.3
  const layout = layoutOf(obj);
  if (layout === Layout.MODAL_DFC) {
    return faces.flatMap((f, i) => ((f.type_line.types & CardType.LAND) === 0 && f.has_mana_cost ? [i] : []));
  }
  if (layout === Layout.SPLIT || layout === Layout.ADVENTURE) return faces.flatMap((f, i) => (f.has_mana_cost ? [i] : []));
  if (layout === Layout.PROTOTYPE) {
    // CR 718.3: index 1 is the prototype face the card data doesn't print.
    return faces.length > 1 || hasPrototypeLine(faces[0]!) ? [0, 1] : [0];
  }
  return [0]; // CR 712.11
}

/** Faces that may be played as a land (CR 712.12). */
export function playableLandFaceIndices(_game: Game, obj: GameObject): number[] {
  const faces = facesOf(obj);
  if (faces.length === 0) return [];
  if (layoutOf(obj) === Layout.MODAL_DFC) return faces.flatMap((f, i) => ((f.type_line.types & CardType.LAND) !== 0 ? [i] : []));
  return (faces[0]!.type_line.types & CardType.LAND) !== 0 ? [0] : [];
}
