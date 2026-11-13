/**
 * Protection, hexproof and shroud for objects and players (CR 702.11,
 * 702.16, 702.18). Ported from mtgfish/rules/cr100_game_concepts/actions.py
 * (_protected_from, player_abilities, _static_player_grants,
 * player_shielded_from, player_protected_from).
 */

import type { Ability, Effect, ObjectFilter } from "../../ir";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { abilityKeyword, keywordAbilities } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { flag, ir, list, num } from "../ir_read";

const GRANT_ABILITY: number = ir("EffectKind.GRANT_ABILITY");
const STATIC: number = ir("AbilityKind.STATIC");

const isObjectFilter = (q: unknown): q is ObjectFilter => (q as { $?: string } | null)?.$ === "ObjectFilter";

function sourceMatches(game: Game, source: GameObject, quality: unknown): boolean {
  if (!isObjectFilter(quality)) return false;
  return matchHooks().matches(game, source, quality, { allowStale: true });
}

/** CR 702.16: whether `target` has protection from `source`. */
export function protectedFrom(game: Game, target: GameObject, source: GameObject | null): boolean {
  if (source === null) return false;
  // CR 702.16a: protection whose quality was not read states none, matches nothing.
  return keywordAbilities(characteristicsOf(game, target), "Protection").some(
    (a) => a.quality != null && sourceMatches(game, source, a.quality),
  );
}

/** The abilities a player has from effects (CR 702.11a, 702.16a, 702.18a). */
export function playerAbilities(game: Game, playerId: PlayerId): Ability[] {
  const out: Ability[] = [];
  for (const ce of game.continuousEffects) {
    const effect = ce.effect as Effect;
    const specific = list(effect.targets?.specific);
    if (
      num(effect.kind) === GRANT_ABILITY &&
      effect.players != null &&
      effect.players.specific === playerId &&
      effect.targets != null &&
      specific.length === 1 &&
      specific[0] === NO_OBJECT
    ) {
      out.push(...list(effect.granted_abilities));
    }
  }
  out.push(...staticPlayerGrants(game, playerId));
  return out;
}

/** CR 604.1, 611.3a: "you have hexproof" from a permanent's static ability. */
function staticPlayerGrants(game: Game, playerId: PlayerId): Ability[] {
  const hooks = matchHooks();
  const out: Ability[] = [];
  for (const id of game.battlefield) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      const grants = list(ability.effects).filter(
        (e) => num(e.kind) === GRANT_ABILITY && e.players != null && e.targets == null,
      );
      if (grants.length === 0) continue;
      const scope = { source: obj.id, controller: obj.controller };
      const condition = ability.static_condition;
      if (condition !== undefined && num(condition.kind) !== 0 && !hooks.holds(game, condition, scope)) continue;
      for (const effect of grants) {
        if (hooks.resolvePlayers(game, effect.players!, scope).includes(playerId)) {
          out.push(...list(effect.granted_abilities));
        }
      }
    }
  }
  return out;
}

/**
 * Whether a player can't be targeted by (or, for protection, dealt damage
 * by) this source: protection (CR 702.16b, e), hexproof against an
 * opponent's source (702.11b), shroud (702.18a).
 */
export function playerShieldedFrom(
  game: Game,
  playerId: PlayerId,
  source: GameObject | null,
  sourceController: PlayerId,
): boolean {
  for (const ability of playerAbilities(game, playerId)) {
    const keyword = abilityKeyword(ability).toLowerCase();
    if (keyword === "shroud") return true;
    if (keyword === "hexproof" && sourceController !== playerId) return true;
    if (keyword === "protection" && source !== null && ability.quality != null && sourceMatches(game, source, ability.quality)) {
      return true;
    }
  }
  return false;
}

/** CR 702.16e: damage from a source the player has protection from. */
export function playerProtectedFrom(game: Game, playerId: PlayerId, source: GameObject | null): boolean {
  if (source === null) return false;
  return playerAbilities(game, playerId).some(
    (a) => abilityKeyword(a).toLowerCase() === "protection" && a.quality != null && sourceMatches(game, source, a.quality),
  );
}
