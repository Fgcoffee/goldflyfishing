/**
 * Rules of particular card types. Ported from
 * mtgfish/rules/cr300_card_types/cr300_card_types.py, a piece at a time.
 */

import { permanents, type Game } from "../../kernel/game";
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
