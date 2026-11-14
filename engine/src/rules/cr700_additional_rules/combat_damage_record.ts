/**
 * What dealt combat damage to a player this turn, as it was then (CR
 * 702.76a prowl, 702.173a freerunning). Ported from
 * cr702_cost_keyword_rules.py (record_combat_damage_source, _damage_records,
 * combat_damage_dealt_by, combat_damage_by_shared_type). Kept in
 * `game.turnHistory` under a key that starts "combat damage by|".
 */

import type { Condition } from "../../ir";
import { CardType } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import { NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { creatureTypesOf } from "../cr200_parts_of_a_card/typeline";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { num, strs } from "../ir_read";

export const COMBAT_DAMAGE_BY = "combat damage by";

/** Remember what dealt combat damage to a player, as it was at that moment. */
export function recordCombatDamageSource(game: Game, sourceId: ObjectId, sourceController: PlayerId): void {
  const obj = game.objects.get(sourceId);
  if (obj === undefined || sourceController === NO_PLAYER) return;
  const chars = characteristicsOf(game, obj);
  const types = [...creatureTypesOf(chars.typeLine)].sort();
  const creature = (chars.typeLine.types & CardType.CREATURE) !== 0;
  const key = [COMBAT_DAMAGE_BY, sourceController, obj.isCommander ? 1 : 0, creature ? 1 : 0, types.join(",")].join("|");
  game.turnHistory.set(key, (game.turnHistory.get(key) ?? 0) + 1);
}

/** [was a commander, was a creature, creature types] for each record of this controller. */
function* damageRecords(game: Game, controller: PlayerId): Generator<[boolean, boolean, string[]]> {
  for (const [key, count] of game.turnHistory) {
    if (!count || !key.startsWith(`${COMBAT_DAMAGE_BY}|`)) continue;
    const [, who, commander, creature, types] = key.split("|");
    if (Number(who) !== controller) continue;
    yield [commander === "1", creature === "1", types ? types.split(",") : []];
  }
}

/** COMBAT_DAMAGE_DEALT_BY: by a source you controlled that matched the condition's filter then. */
export function combatDamageDealtBy(game: Game, condition: Condition, controller: PlayerId): boolean {
  const spec = condition.filter ?? null;
  for (const [wasCommander, wasCreature, types] of damageRecords(game, controller)) {
    if (spec === null) return true;
    if (spec.is_commander != null && spec.is_commander !== wasCommander) continue;
    if (num(spec.types_all) & CardType.CREATURE && !wasCreature) continue;
    const wanted = new Set(strs(spec.subtypes_any).map((n) => n.toLowerCase()));
    if (wanted.size > 0 && !types.some((t) => wanted.has(t.toLowerCase()))) continue;
    return true;
  }
  return false;
}

/** COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE (CR 702.76a): by a source sharing a creature type with this. */
export function combatDamageBySharedType(game: Game, sourceId: ObjectId, controller: PlayerId): boolean {
  const obj = game.objects.get(sourceId);
  if (obj === undefined) return false;
  const mine = new Set(creatureTypesOf(characteristicsOf(game, obj).typeLine).map((n) => n.toLowerCase()));
  if (mine.size === 0) return false;
  for (const [, , types] of damageRecords(game, controller)) {
    if (types.some((t) => mine.has(t.toLowerCase()))) return true;
  }
  return false;
}

