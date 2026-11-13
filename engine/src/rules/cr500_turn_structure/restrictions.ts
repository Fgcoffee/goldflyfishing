/**
 * Restrictions, requirements and permissions (CR 101.2, 113.6): "can't",
 * "must", "may ... as though". PORT: placeholder - the full port of
 * mtgfish/rules/cr500_turn_structure/restrictions.py comes with casting and
 * combat (ROADMAP "rules"). Until then nothing is prohibited or permitted.
 */

import { ActValues } from "../../ir";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";

/** What a restriction can forbid or allow: the IR's Act enum. */
export const Act = {
  CAST_SPELL: ActValues.CAST_SPELL as number,
  PLAY_LAND: ActValues.PLAY_LAND as number,
  CAST_AS_THOUGH_FLASH: ActValues.CAST_AS_THOUGH_FLASH as number,
  ACTIVATE_ABILITY: ActValues.ACTIVATE_ABILITY as number,
  DRAW_CARD: ActValues.DRAW_CARD as number,
  UNTAP: ActValues.UNTAP as number,
  UNTAP_DURING_UNTAP_STEP: ActValues.UNTAP_DURING_UNTAP_STEP as number,
  DISCARD_TO_HAND_SIZE: ActValues.DISCARD_TO_HAND_SIZE as number,
} as const;
export type Act = number;

export interface ActContext {
  readonly obj?: GameObject;
  readonly player?: PlayerId;
  readonly counterpart?: GameObject;
}

/** The restriction forbidding this act, or null. PORT: placeholder. */
export function prohibited(_game: Game, _act: Act, _context: ActContext = {}): unknown {
  return null;
}

/** The permission allowing this act, or null. PORT: placeholder. */
export function permitted(_game: Game, _act: Act, _context: ActContext = {}): unknown {
  return null;
}
