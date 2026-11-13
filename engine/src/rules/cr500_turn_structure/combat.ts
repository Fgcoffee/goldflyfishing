/**
 * Combat (CR 506-511). PORT: placeholder - mtgfish/rules/cr500_turn_structure/
 * cr506_combat.py is the "Combat" item of ROADMAP "rules". Until then nobody
 * attacks, so the declare blockers and damage steps are skipped (CR 508.8).
 */

import type { Game } from "../../kernel/game";
import type { ObjectId, PlayerId } from "../../kernel/ids";

/** The combat in progress: attacker -> the player it attacks. */
export interface Combat {
  attacking: Map<ObjectId, PlayerId>;
}

export function attackers(game: Game): ReadonlyMap<ObjectId, PlayerId> {
  return (game.combat as Combat | null)?.attacking ?? new Map();
}

export function declareAttackers(_game: Game): void {}
export function declareBlockers(_game: Game): void {}
export function dealCombatDamage(_game: Game): void {}
export function endCombat(game: Game): void {
  game.combat = null;
}
