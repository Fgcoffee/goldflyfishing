/**
 * Casting spells, activating abilities and playing lands (CR 601, 602, 305).
 * PORT: placeholder - mtgfish/rules/cr600_spells_and_abilities/cr601_casting.py
 * is the "Casting spells and activating abilities" item of ROADMAP "rules".
 * Until then every attempt is abandoned with a CastError, which the priority
 * loop treats as an action that did not happen (CR 601.2h).
 */

import type { Game } from "../../kernel/game";
import type { PlayerId } from "../../kernel/ids";
import type { Action } from "../cr100_game_concepts/priority";

/** An attempt that could not be completed; CR 601.2h has rewound it. */
export class CastError extends Error {}

export function castSpell(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("casting is not ported yet");
}

export function activateAbility(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("activating abilities is not ported yet");
}

export function playLand(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("playing lands is not ported yet");
}
