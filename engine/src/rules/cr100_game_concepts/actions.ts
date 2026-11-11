/**
 * Game actions shared by many rules. Ported from
 * mtgfish/rules/cr100_game_concepts/actions.py, one action at a time as the
 * rules that use them are ported.
 */

import { event, EventKind } from "../../kernel/events";
import { invalidateAllCharacteristics, type Game } from "../../kernel/game";
import { addCounters as putCounters, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId } from "../../kernel/ids";
import { emit } from "../emit";
import { replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";

/**
 * Put counters on an object, through replacement effects (CR 122.6, 614.1a).
 * CR 107.1b: a calculated amount below zero puts none on, it doesn't remove.
 * Returns how many were added.
 */
export function addCounters(
  game: Game,
  obj: GameObject,
  kind: string,
  amount: number,
  options: { source?: ObjectId } = {},
): number {
  if (amount <= 0) return 0;
  const source = options.source ?? NO_OBJECT;
  let count = amount;
  const hooks = replacementHooks();
  if (hooks.hasReplacements(game)) {
    const prospective = hooks.replace(
      game,
      event(EventKind.COUNTER_ADDED, { objectId: obj.id, player: obj.controller, source, amount: count, data: [kind] }),
    );
    if (prospective === null) return 0;
    count = prospective.amount;
    if (count <= 0) return 0;
  }
  const added = putCounters(obj, kind, count);
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.COUNTER_ADDED, { objectId: obj.id, player: obj.controller, source, amount: added, data: [kind] }));
  return added;
}
