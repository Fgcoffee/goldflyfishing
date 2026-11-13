/**
 * When a continuous effect or standing restriction stops applying
 * (CR 611.2b). Ported from mtgfish/rules/cr600_spells_and_abilities/
 * cr611_durations.py. Checked at three moments: as a turn begins, as the end
 * of combat step ends, and in the cleanup step.
 *
 * The "next turn" durations record the turn they began on, and ask whether
 * their controller has since begun a later turn - extra turns (CR 500.7) make
 * that unknowable when the effect is created.
 */

import { Duration } from "../../kernel/enums";
import { invalidateAllCharacteristics, player, type Game } from "../../kernel/game";
import { NO_PLAYER, type PlayerId } from "../../kernel/ids";

/** Anything with a duration: effects, restrictions, permissions, replacements. */
interface Timed {
  readonly duration: number;
  readonly createdTurn: number;
  readonly controller?: PlayerId;
  readonly used?: boolean;
}

/** Is this its controller's turn, and a later one than it began on? */
function controllersLaterTurn(game: Game, entry: Timed): boolean {
  let controller = entry.controller ?? NO_PLAYER;
  if (controller === NO_PLAYER) controller = game.activePlayer;
  if (player(game, controller).hasLost) return true;
  return game.activePlayer === controller && game.turn > entry.createdTurn;
}

function isOver(game: Game, entry: Timed, moment: Duration): boolean {
  const d = entry.duration;
  if (moment === Duration.END_OF_COMBAT) return d === Duration.END_OF_COMBAT;
  if (moment === Duration.YOUR_NEXT_TURN) {
    if (d === Duration.END_OF_COMBAT) return true;
    return d === Duration.YOUR_NEXT_TURN && controllersLaterTurn(game, entry);
  }
  if (moment === Duration.END_OF_TURN) {
    if (d === Duration.END_OF_TURN) return true;
    return d === Duration.END_OF_YOUR_NEXT_TURN && controllersLaterTurn(game, entry);
  }
  return false;
}

function expire(game: Game, moment: Duration): void {
  let changed = false;
  for (const effect of game.continuousEffects) {
    if (!effect.expired && isOver(game, effect, moment)) {
      effect.expired = true;
      changed = true;
    }
  }
  if (changed) game.continuousEffects = game.continuousEffects.filter((e) => !e.expired);

  const restrictions = (game.standingRestrictions as Timed[]).filter((r) => !isOver(game, r, moment));
  if (restrictions.length !== game.standingRestrictions.length) {
    game.standingRestrictions = restrictions;
    game.restrictionsCache = null;
    changed = true;
  }
  const requirements = (game.standingRequirements as Timed[]).filter((r) => !isOver(game, r, moment));
  if (requirements.length !== game.standingRequirements.length) game.standingRequirements = requirements;
  const permissions = (game.standingPermissions as Timed[]).filter((p) => !isOver(game, p, moment));
  if (permissions.length !== game.standingPermissions.length) {
    game.standingPermissions = permissions;
    game.permissionsEpoch = -1;
    changed = true;
  }
  const replacements = (game.replacementEffects as Timed[]).filter((r) => !r.used && !isOver(game, r, moment));
  if (replacements.length !== game.replacementEffects.length) game.replacementEffects = replacements;
  if (changed) invalidateAllCharacteristics(game);
}

/** CR 511.2, 511.3: "until end of combat", as the end of combat step ends. */
export function expireAtEndOfCombat(game: Game): void {
  expire(game, Duration.END_OF_COMBAT);
}

/** "Until your next turn" ends as that turn begins (CR 611.2b). */
export function expireAtStartOfTurn(game: Game): void {
  expire(game, Duration.YOUR_NEXT_TURN);
}

/** CR 514.2: "until end of turn" and "until the end of your next turn". */
export function expireAtCleanup(game: Game): void {
  expire(game, Duration.END_OF_TURN);
}
