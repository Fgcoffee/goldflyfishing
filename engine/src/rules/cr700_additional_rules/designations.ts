/**
 * The monarch, the initiative, day and night, rad counters (CR 724-728).
 * PORT: placeholder - mtgfish/rules/cr700_additional_rules/
 * cr725_designations.py is ported with the keyword runtime (ROADMAP "rules").
 * Until then these do nothing.
 */

import type { Game } from "../../kernel/game";
import type { PlayerId } from "../../kernel/ids";

export function checkDayNightTransition(_game: Game, _active: PlayerId): void {}
export function radCounterMilling(_game: Game, _active: PlayerId): void {}
export function initiativeUpkeep(_game: Game): void {}
export function monarchEndStepDraw(_game: Game): void {}
export function monarchLeftTheGame(_game: Game): void {}
export function initiativeLeftTheGame(_game: Game): void {}
