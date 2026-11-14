/**
 * Preparation (CR 722). Ported from
 * mtgfish/rules/cr700_additional_rules/cr722_preparation.py.
 *
 * A preparation card prints a second set of characteristics in an inset
 * frame, its "prepare spell" (CR 722.2a). The card is never cast as that
 * spell (CR 722.3); a permanent with one can become prepared, and while it
 * is, a copy of the prepare spell waits in exile for its controller to cast
 * (CR 722.3c).
 */

import type { CardDef } from "../../kernel/card";
import { Layout, Zone } from "../../kernel/enums";
import { createObject, invalidateAllCharacteristics, logRecord, type Game } from "../../kernel/game";
import { isLive, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId } from "../../kernel/ids";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";

/** The face that holds the prepare spell (CR 722.2): the card data's second face. */
export const PREPARE_FACE = 1;

/** CR 722.2a: whether the object has a prepare spell to be prepared with. */
export function hasPrepareSpell(obj: GameObject): boolean {
  const card = obj.card as Partial<CardDef> | null;
  return card != null && card.layout === Layout.PREPARE && (card.faces?.length ?? 0) > PREPARE_FACE;
}

export function isPrepared(game: Game, objectId: ObjectId): boolean {
  return game.preparedPermanents.has(objectId);
}

/**
 * CR 722.3a/c: give a permanent the prepared designation, once, and create
 * the copy of its prepare spell in exile that its controller may cast.
 * Returns the copy, or null if nothing happened.
 */
export function becomePrepared(game: Game, obj: GameObject): GameObject | null {
  if (obj.zone !== Zone.BATTLEFIELD || !isLive(obj)) return null;
  if (!hasPrepareSpell(obj) || isPrepared(game, obj.id)) return null;
  game.preparedPermanents.add(obj.id);
  const copy = createObject(game, obj.card, obj.controller, Zone.EXILE, { kind: ObjectKind.COPY, faceIndex: PREPARE_FACE });
  copy.prepareCopyOf = obj.id;
  // CR 722.3c: the standing permission legality reads, for the prepare face only.
  copy.playableFromHereBy = obj.controller;
  copy.playableFace = PREPARE_FACE;
  invalidateAllCharacteristics(game);
  logRecord(game, `${characteristicsOf(game, obj).name} becomes prepared`, { kind: "prepared", player: obj.controller });
  return copy;
}

/** CR 722.3b: remove the designation; the next SBA check removes the copy. */
export function becomeUnprepared(game: Game, objectId: ObjectId): void {
  game.preparedPermanents.delete(objectId);
}

/** CR 722.3c: the exception to CR 704.5e - the copy stays while its permanent is prepared. */
export function keepsItsPrepareCopy(game: Game, copy: GameObject): boolean {
  if (copy.prepareCopyOf === NO_OBJECT || copy.zone !== Zone.EXILE) return false;
  const permanent = game.objects.get(copy.prepareCopyOf);
  return permanent !== undefined && isLive(permanent) && permanent.zone === Zone.BATTLEFIELD && isPrepared(game, permanent.id);
}
