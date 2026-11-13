/**
 * Face-down permanents (CR 708, 701.40, 701.58). Ported from
 * mtgfish/rules/cr700_additional_rules/cr708_face_down.py: only what special
 * actions needs (mana_cost_to_turn_up, cannot_turn_face_up, turn_face_up,
 * reveal). The face-down characteristics themselves are in layers.ts.
 * Manifest, cloak and turning face down are not ported yet.
 */

import { CardType, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { invalidateAllCharacteristics, logRecord, type Game } from "../../kernel/game";
import { addCounters, invalidateCharacteristics, objectText, type GameObject } from "../../kernel/gameobject";
import { allocateTimestamp } from "../../kernel/ids";
import { hasType } from "../cr200_parts_of_a_card/characteristics";
import { printedCharacteristics, characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { emit } from "../emit";
import { fromIR, type ManaCost } from "../mana/cost";

/** Face-down by these can be turned up for the card's mana cost (CR 701.40b, 701.58b). */
export const TURNED_UP_FOR_MANA_COST: ReadonlySet<string> = new Set(["Manifest", "Cloak"]);

/** The mana cost to turn a manifested or cloaked creature card up, or null. */
export function manaCostToTurnUp(game: Game, obj: GameObject): ManaCost | null {
  if (!obj.faceDown || !TURNED_UP_FOR_MANA_COST.has(obj.faceDownBy)) return null;
  const card = printedCharacteristics(game, obj);
  if (!hasType(card, CardType.CREATURE) || !card.hasManaCost) return null;
  return fromIR(card.manaCost);
}

/** CR 701.40g, 701.58g: an instant or sorcery card stays face down. */
export function cannotTurnFaceUp(game: Game, obj: GameObject): boolean {
  return (printedCharacteristics(game, obj).typeLine.types & (CardType.INSTANT | CardType.SORCERY)) !== 0;
}

/** Show every player the card under a face-down object (CR 708.9). */
export function reveal(game: Game, obj: GameObject, why: string): void {
  const name = obj.card != null ? printedCharacteristics(game, obj).name : "";
  logRecord(game, `face-down #${obj.id} is revealed: ${name} (${why})`, { kind: "reveal", player: obj.owner });
}

/**
 * Turn a face-down permanent face up (CR 708.8): the same object, a new
 * timestamp (CR 613.7f), nothing about entering. A megamorph cost leaves a
 * +1/+1 counter (CR 702.37b).
 */
export function turnFaceUp(game: Game, obj: GameObject, options: { megamorph?: boolean } = {}): boolean {
  if (!obj.faceDown || obj.zone !== Zone.BATTLEFIELD) return false;
  if (cannotTurnFaceUp(game, obj)) {
    reveal(game, obj, "it can't be turned face up");
    return false;
  }
  obj.faceDown = false;
  obj.faceDownBy = "";
  obj.timestamp = allocateTimestamp(game.ids);
  invalidateCharacteristics(obj);
  invalidateAllCharacteristics(game);
  // PORT: CR 208.2b "as this enters or is turned face up" choices
  // (_as_it_is_turned_face_up) come with replacement effects.
  if (options.megamorph) {
    addCounters(obj, "+1/+1", 1);
    invalidateCharacteristics(obj);
    invalidateAllCharacteristics(game);
  }
  logRecord(game, `${objectText(obj, characteristicsOf(game, obj).name)} is turned face up`, { kind: "face-up", player: obj.controller });
  emit(game, event(EventKind.TURNED_FACE_UP, { objectId: obj.id, player: obj.controller }));
  return true;
}
