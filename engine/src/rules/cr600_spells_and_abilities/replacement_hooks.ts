/**
 * Where replacement effects plug into the rest of the rules, until
 * cr614_replacement.py is ported: `Game.has_replacements` / `Game.replace`,
 * the commander zone replacement (CR 903.9), self-entry replacements
 * (CR 614.1c-e), the Attraction junkyard (CR 717.6) and Auras entering
 * without being cast (CR 303.4f-g). Each default changes nothing, so the
 * engine runs - without those rules - until the module that owns them
 * installs the real thing with `installReplacementHooks`.
 */

import type { GameEvent } from "../../kernel/events";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";

export interface ReplacementHooks {
  /** Whether any replacement effect could apply (a fast path). */
  hasReplacements(game: Game): boolean;
  /** CR 614: the event after replacements, or null if it was prevented. */
  replace(game: Game, e: GameEvent): GameEvent | null;
  /** CR 903.9: a commander may go to the command zone instead. */
  commanderZoneReplacement(game: Game, e: GameEvent): GameEvent;
  /** CR 717.6: an Attraction goes to the command zone instead. */
  junkyardReplacement(game: Game, obj: GameObject, e: GameEvent): GameEvent;
  /** CR 614.1c-e: "enters with", "as this enters", applied as it enters. */
  applySelfEntryReplacements(game: Game, obj: GameObject): void;
  /** CR 303.4f-g: does this govern how an Aura not cast enters? */
  auraGovernsEntry(game: Game, obj: GameObject): boolean;
  /** What such an Aura could enchant, as target ids. */
  auraLegalHosts(game: Game, obj: GameObject, playerId: PlayerId): number[];
  /** Attach it to one of `hosts`, chosen by its controller. */
  auraAttachAsItEnters(game: Game, aura: GameObject, hosts: number[]): void;
}

let HOOKS: ReplacementHooks = {
  hasReplacements: () => false,
  replace: (_g, e) => e,
  commanderZoneReplacement: (_g, e) => e,
  junkyardReplacement: (_g, _o, e) => e,
  applySelfEntryReplacements: () => {},
  auraGovernsEntry: () => false,
  auraLegalHosts: () => [],
  auraAttachAsItEnters: () => {},
};

export function installReplacementHooks(hooks: Partial<ReplacementHooks>): void {
  HOOKS = { ...HOOKS, ...hooks };
}

export function replacementHooks(): ReplacementHooks {
  return HOOKS;
}
