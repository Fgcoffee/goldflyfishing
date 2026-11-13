/**
 * Resolving the top of the stack (CR 608). PORT: placeholder -
 * mtgfish/rules/cr600_spells_and_abilities/cr608_stack.py and resolve.py are
 * the "Resolving the stack" item of ROADMAP "rules". Nothing reaches the stack
 * until casting is ported, so reaching this says so plainly.
 */

import type { Game } from "../../kernel/game";

export function resolveTop(_game: Game): void {
  throw new Error("resolving the stack is not ported yet (rules/cr600_spells_and_abilities/stack.ts)");
}
