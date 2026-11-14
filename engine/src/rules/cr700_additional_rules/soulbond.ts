/**
 * Soulbond and paired creatures (CR 702.95). Ported from
 * mtgfish/rules/cr700_additional_rules/cr702_soulbond.py.
 *
 * A pair is two creatures on the battlefield under one player's control,
 * each recording the other in `pairedWith`. CR 702.95e ends the pair the
 * moment either stops being a creature, leaves, or changes controller.
 * `isStillPaired` answers that without changing anything (the layer system
 * asks it); `endBrokenPairs`, run beside the state-based actions, clears
 * what has ended, so a creature that becomes one again isn't paired again.
 */

import { Zone } from "../../kernel/enums";
import { invalidateAllCharacteristics, logRecord, type Game } from "../../kernel/game";
import { isLive, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { isCreature } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";

function pairable(game: Game, obj: GameObject | undefined, controller: PlayerId): boolean {
  return obj !== undefined && isLive(obj) && obj.zone === Zone.BATTLEFIELD && obj.controller === controller && isCreature(characteristicsOf(game, obj));
}

/** CR 702.95b, 702.95e: whether this creature is paired now. */
export function isStillPaired(game: Game, obj: GameObject): boolean {
  if (obj.pairedWith === NO_OBJECT) return false;
  const partner = game.objects.get(obj.pairedWith);
  return (
    partner !== undefined &&
    partner.pairedWith === obj.id &&
    pairable(game, obj, obj.pairController) &&
    pairable(game, partner, obj.pairController)
  );
}

/** The creature this one is paired with, if the pair still holds. */
export function partnerOf(game: Game, obj: GameObject): GameObject | null {
  return isStillPaired(game, obj) ? (game.objects.get(obj.pairedWith) ?? null) : null;
}

/**
 * CR 702.95c/d: pair two creatures, if both are still creatures on the
 * battlefield under this player's control and neither is already paired.
 */
export function pair(game: Game, first: GameObject, second: GameObject, controller: PlayerId): boolean {
  if (first.id === second.id) return false;
  if (!(pairable(game, first, controller) && pairable(game, second, controller))) return false;
  if (isStillPaired(game, first) || isStillPaired(game, second)) return false;
  for (const [one, other] of [[first, second], [second, first]] as const) {
    one.pairedWith = other.id;
    one.pairController = controller;
  }
  invalidateAllCharacteristics(game);
  logRecord(game, `${characteristicsOf(game, first).name} is paired with ${characteristicsOf(game, second).name}`, { kind: "pair", player: controller });
  return true;
}

/** CR 702.95e: clear every pair that has ended; whether any did. */
export function endBrokenPairs(game: Game): boolean {
  let ended = false;
  for (const id of [...game.battlefield]) {
    const obj = game.objects.get(id);
    if (obj !== undefined && obj.pairedWith !== NO_OBJECT && !isStillPaired(game, obj)) {
      obj.pairedWith = NO_OBJECT;
      ended = true;
    }
  }
  if (ended) invalidateAllCharacteristics(game);
  return ended;
}
