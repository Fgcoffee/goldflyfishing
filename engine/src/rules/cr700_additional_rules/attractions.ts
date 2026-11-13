/**
 * Attractions (CR 717). PORT: placeholder - mtgfish/rules/
 * cr700_additional_rules/cr717_attractions.py is ported with the keyword
 * runtime. Until then nobody controls an Attraction.
 */

import type { Game } from "../../kernel/game";
import type { PlayerId } from "../../kernel/ids";

export function controlsAnAttraction(_game: Game, _player: PlayerId): boolean {
  return false;
}

export function rollToVisit(_game: Game, _player: PlayerId): void {}
