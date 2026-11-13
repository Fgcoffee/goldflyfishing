/**
 * What a player with priority may legally do. PORT: placeholder -
 * mtgfish/rules/kernel/legality.py comes with casting (ROADMAP "rules").
 * Until then nothing but passing is legal.
 */

import type { Game } from "../kernel/game";
import type { PlayerId } from "../kernel/ids";
import type { Action } from "./cr100_game_concepts/priority";

export function legalActions(_game: Game, _player: PlayerId): Action[] {
  return [];
}
