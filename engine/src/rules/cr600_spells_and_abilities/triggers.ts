/**
 * Triggered abilities (CR 603). PORT: placeholder - mtgfish/rules/
 * cr600_spells_and_abilities/cr603_triggers.py is the "Triggered abilities"
 * item of ROADMAP "rules". Until then nothing triggers.
 */

import type { Game } from "../../kernel/game";

/** Put waiting triggered abilities on the stack (CR 603.3b); how many. */
export function putTriggersOnStack(_game: Game): number {
  return 0;
}

/** CR 603.8: state triggers. */
export function checkStateTriggers(_game: Game): void {}

/** CR 605.4a: triggered mana abilities resolve at once; how many did. PORT: placeholder. */
export function resolveManaTriggers(_game: Game): number {
  return 0;
}
