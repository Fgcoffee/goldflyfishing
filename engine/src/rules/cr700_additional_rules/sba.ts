/**
 * State-based actions (CR 704). Ported from
 * mtgfish/rules/cr700_additional_rules/cr704_sba.py.
 *
 * Whenever a player would receive priority the game checks every
 * state-based action, performs all that apply simultaneously, and checks
 * again until none apply (CR 704.3). Everything in one round is decided
 * before any of it is done: two creatures that would each kill the other
 * both die. They don't use the stack (CR 704.2).
 *
 * `// PENDING(rules): ...` and `// PORT:` mark what needs a record or module
 * the TS engine doesn't have yet (engine/status/parser.md lists them).
 */

import type { Ability, ObjectFilter, PlayerFilter } from "../../ir";
import { CardType, LossReason, Supertype, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import {
  agentFor,
  invalidateAllCharacteristics,
  logRecord,
  permanents,
  player,
  type Game,
} from "../../kernel/game";
import { counterCount, isLive, isPermanent, ObjectKind, removeCounters, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { num } from "../ir_read";
import { destroy, detach, isIndestructible, playerLoses, putIntoGraveyard, sacrifice } from "../cr100_game_concepts/actions";
import { lethalCommanderDamage, startEngines } from "../cr100_game_concepts/player";
import { playerProtectedFrom, protectedFrom } from "../cr100_game_concepts/protection";
import {
  abilityKeyword,
  hasKeyword,
  hasSubtype,
  hasSupertype,
  hasType,
  isCreature,
  type Characteristics,
} from "../cr200_parts_of_a_card/characteristics";
import { Act, prohibited } from "../cr500_turn_structure/restrictions";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { enchantableScope } from "../cr600_spells_and_abilities/targeting";
import { PlayerScope } from "../match/enums";
import { chooseProtector, eligibleProtectors, moveObject, removeFromZone } from "../zones";

/** Guard against an effect loop that keeps generating work forever. */
export const MAX_ITERATIONS = 100;

/**
 * Run state-based actions to a fixed point (CR 704.3). Returns whether
 * anything was done, which the priority loop uses to decide whether to
 * check triggers again before handing out priority.
 */
export function checkStateBasedActions(game: Game): boolean {
  let didAnything = false;
  for (let i = 0; i < MAX_ITERATIONS; i++) {
    // PORT: Python calls check_removal_from_combat(game) here (CR 506.4); it
    // comes with the rules session's combat port.
    // CR 704.3: one round is simultaneous - every creature it puts into a
    // graveyard dies at the same time (CR 603.2c).
    game.eventBatch += 1;
    if (!onePass(game)) break;
    didAnything = true;
    if (game.gameOver) break;
  }
  return didAnything;
}

function onePass(game: Game): boolean {
  const losers: [PlayerId, LossReason][] = [];
  const toGraveyard: GameObject[] = [];
  const toDestroy: GameObject[] = [];
  const toCease: GameObject[] = [];
  const toUnattach: GameObject[] = [];
  const counterAnnihilation: GameObject[] = [];
  const legendChoices: [PlayerId, string, GameObject[]][] = [];
  const toSacrifice: GameObject[] = [];
  const speedStarts: PlayerId[] = [];

  const commanderMoves = checkCommanderZoneChoice(game);

  grantEnduringStories(game);
  // CR 702.95e: a pair ends the moment either creature stops qualifying.
  endBrokenPairs(game);
  checkPlayers(game, losers);
  checkPermanents(game, toGraveyard, toDestroy, toUnattach, counterAnnihilation, toSacrifice);
  checkCeased(game, toCease);
  checkSpeed(game, speedStarts);
  checkLegendRule(game, legendChoices);
  checkWorldRule(game, toGraveyard);
  checkRoleRule(game, toGraveyard);
  // PENDING(rules): CR 704.5t, finished dungeons (cr309_dungeons.py isn't ported).

  if (
    losers.length + toGraveyard.length + toDestroy.length + toCease.length + toUnattach.length +
      counterAnnihilation.length + legendChoices.length + toSacrifice.length + speedStarts.length +
      commanderMoves.length ===
    0
  ) {
    return false;
  }

  for (const playerId of speedStarts) {
    if (startEngines(player(game, playerId))) {
      logRecord(game, `${player(game, playerId).name} starts their engines`, { kind: "sba" });
    }
  }

  for (const obj of commanderMoves) moveCommanderHome(game, obj);

  // -- perform, all at once ----------------------------------------------
  // CR 704.8: a permanent leaving in this same round keeps its counters, so
  // its last known information is the state before any of this happened
  // (a Young Wolf with +1/+1 and -1/-1 counters dies with its +1/+1).
  const leaving = new Set<ObjectId>();
  for (const group of [toGraveyard, toDestroy, toSacrifice, toCease]) for (const obj of group) leaving.add(obj.id);

  for (const obj of counterAnnihilation) {
    if (leaving.has(obj.id)) continue;
    // CR 704.5q: +1/+1 and -1/-1 counters cancel in pairs.
    const pairs = Math.min(counterCount(obj, "+1/+1"), counterCount(obj, "-1/-1"));
    if (pairs) {
      removeCounters(obj, "+1/+1", pairs);
      removeCounters(obj, "-1/-1", pairs);
      invalidateAllCharacteristics(game);
    }
  }

  for (const obj of toUnattach) detach(game, obj);

  for (const obj of toCease) {
    logRecord(game, `${objectName(game, obj)} ceases to exist`, { kind: "sba" });
    removeFromZone(game, obj);
    if (obj.kind === ObjectKind.TOKEN && game.pendingTriggers.some((p) => pendingSource(p) === obj.id)) {
      // Its own death trigger still needs the token's controller and
      // last-known characteristics (CR 603.3a, 603.10a): kept like a card's
      // pre-move object - not live, reading the zone it left.
      obj.supersededBy = obj.id;
      obj.zone = Zone.BATTLEFIELD;
    } else {
      game.objects.delete(obj.id);
    }
    invalidateAllCharacteristics(game);
  }

  for (const obj of toGraveyard) if (obj.zone === Zone.BATTLEFIELD) putIntoGraveyard(game, obj);
  for (const obj of toDestroy) destroy(game, obj);
  for (const obj of toSacrifice) sacrifice(game, obj);
  for (const [controller, name, group] of legendChoices) resolveLegendRule(game, controller, name, group);
  for (const [playerId, reason] of losers) playerLoses(game, playerId, reason);
  return true;
}

function objectName(game: Game, obj: GameObject): string {
  return characteristicsOf(game, obj).name || `object ${obj.id}`;
}

function pendingSource(pending: unknown): ObjectId {
  const source = (pending as { source?: unknown } | null)?.source;
  return typeof source === "number" ? source : NO_OBJECT;
}

// ---------------------------------------------------------------------------
// Players (CR 704.5a-c, 903.9a, 903.10)
// ---------------------------------------------------------------------------

/**
 * CR 903.9a: a commander newly in a graveyard or exile may go home. The
 * window is exactly one check: a commander that has sat through one stays.
 */
function checkCommanderZoneChoice(game: Game): GameObject[] {
  const pending = game.commandersAwaitingZoneChoice;
  if (pending.length === 0) return [];
  game.commandersAwaitingZoneChoice = [];
  const moves: GameObject[] = [];
  for (const id of pending) {
    const obj = game.objects.get(id);
    if (obj === undefined || !isLive(obj)) continue;
    if (obj.zone !== Zone.GRAVEYARD && obj.zone !== Zone.EXILE) continue; // moved on already
    if (wantsCommandZone(game, obj, obj.zone)) moves.push(obj);
  }
  return moves;
}

/**
 * Whether the commander's owner takes the CR 903.9 option: the agent's
 * `moveCommanderToCommandZone(game, owner, obj, zone)` if it has one, else
 * yes (Python `cr614_replacement.wants_command_zone`).
 */
export function wantsCommandZone(game: Game, obj: GameObject, toZone: Zone): boolean {
  const agent = agentFor(game, obj.owner) as {
    moveCommanderToCommandZone?: (game: Game, owner: PlayerId, obj: GameObject, zone: Zone) => boolean;
  } | null | undefined;
  if (agent && typeof agent.moveCommanderToCommandZone === "function") {
    return Boolean(agent.moveCommanderToCommandZone(game, obj.owner, obj, toZone));
  }
  return true;
}

function moveCommanderHome(game: Game, obj: GameObject): void {
  const from = obj.zone;
  logRecord(game, `${objectName(game, obj)} moves to the command zone (CR 903.9a)`, { kind: "commander", player: obj.owner });
  const moved = moveObject(game, obj, Zone.COMMAND, { toPlayer: obj.owner });
  emit(game, event(EventKind.COMMANDER_MOVED_TO_COMMAND_ZONE, { objectId: moved.id, player: obj.owner, fromZone: from }));
}

function checkPlayers(game: Game, losers: [PlayerId, LossReason][]): void {
  // The sandbox switches these off together. Returning before the loop
  // matters: a spared loser would still be pending work on every pass.
  if (game.relaxations.playersCannotLose) return;
  for (const p of game.players) {
    if (p.hasLost) continue;
    if (p.life <= 0) losers.push([p.id, LossReason.LIFE]); // CR 704.5a
    else if (p.attemptedDrawFromEmptyLibrary) losers.push([p.id, LossReason.EMPTY_LIBRARY]); // CR 704.5b
    else if (p.poison >= 10) losers.push([p.id, LossReason.POISON]); // CR 704.5c
    // CR 704.6c (CR 903.10a): 21 or more combat damage from one commander.
    else if (lethalCommanderDamage(p) !== NO_OBJECT) losers.push([p.id, LossReason.COMMANDER_DAMAGE]);
  }
}

// ---------------------------------------------------------------------------
// Permanents (CR 704.5f-s, v-y)
// ---------------------------------------------------------------------------

function checkPermanents(
  game: Game,
  toGraveyard: GameObject[],
  toDestroy: GameObject[],
  toUnattach: GameObject[],
  counterAnnihilation: GameObject[],
  toSacrifice: GameObject[],
): void {
  for (const obj of permanents(game)) {
    const chars = characteristicsOf(game, obj);

    // CR 704.5q, before lethal damage: it can change toughness.
    if (counterCount(obj, "+1/+1") && counterCount(obj, "-1/-1")) counterAnnihilation.push(obj);

    if (isCreature(chars)) {
      const toughness = chars.toughness ?? 0;
      // CR 704.5f: not destruction, so indestructible doesn't save it.
      if (toughness <= 0) {
        toGraveyard.push(obj);
        continue;
      }
      // CR 704.5g, 704.5h: lethal damage, or any deathtouch damage.
      if (!isIndestructible(game, obj)) {
        if ((obj.damage >= toughness || obj.dealtDeathtouchDamage) && obj.damage > 0) {
          toDestroy.push(obj);
          continue;
        }
      }
    }

    // CR 704.5i
    if (hasType(chars, CardType.PLANESWALKER) && counterCount(obj, "loyalty") <= 0) {
      toGraveyard.push(obj);
      continue;
    }

    if (hasType(chars, CardType.BATTLE)) {
      // CR 704.5w; 704.5v spares a Siege whose own trigger hasn't left.
      if (counterCount(obj, "defense") <= 0 && !(hasSubtype(chars, "Siege") && isSourceOnTheStack(game, obj))) {
        toGraveyard.push(obj);
        continue;
      }
      // CR 704.5x, 704.5y: a new protector, or the graveyard if nobody can
      // be. 704.5x waits while it's attacked (CR 310.9d).
      if (!hasEligibleProtector(game, obj) && !isBeingAttacked(game, obj)) {
        if (!chooseProtector(game, obj)) {
          toGraveyard.push(obj);
          continue;
        }
      }
    }

    // CR 704.5m: an Aura on something illegal, or on nothing.
    if (hasSubtype(chars, "Aura")) {
      if (!attachmentIsLegal(game, obj, true)) {
        if (obj.bestowed) {
          // CR 702.103f: a bestowed Aura becomes unattached and a creature again.
          obj.bestowed = false;
          toUnattach.push(obj);
          invalidateAllCharacteristics(game);
          continue;
        }
        toGraveyard.push(obj);
        continue;
      }
    }

    // CR 704.5n: Equipment and Fortifications fall off an illegal host.
    // CR 704.5p: anything else that isn't an Aura falls off whatever it's on.
    if (hasSubtype(chars, "Equipment") || hasSubtype(chars, "Fortification")) {
      if (obj.attachedTo !== NO_OBJECT && !attachmentIsLegal(game, obj, false)) toUnattach.push(obj);
    } else if ((obj.attachedTo !== NO_OBJECT || obj.attachedPlayer !== NO_PLAYER) && !hasSubtype(chars, "Aura")) {
      toUnattach.push(obj);
    }

    // CR 704.5s: a finished Saga with no chapter ability waiting.
    if (hasSubtype(chars, "Saga") && sagaIsFinished(game, obj, chars)) toSacrifice.push(obj);
  }
}

function isPlayerFilter(quality: unknown): quality is PlayerFilter {
  return (quality as { $?: string } | null)?.$ === "PlayerFilter";
}

/**
 * Whether an attachment is on something it may be on (CR 303.4c, 301.5,
 * 301.6). The restriction is the Enchant / Equip / Fortify keyword's
 * `quality`; one the parser couldn't read leaves the attachment in place.
 */
export function attachmentIsLegal(game: Game, obj: GameObject, requireHost: boolean): boolean {
  const chars = characteristicsOf(game, obj);
  if (obj.attachedPlayer !== NO_PLAYER) return enchantedPlayerIsLegal(game, obj, chars);
  if (obj.attachedTo === NO_OBJECT) return !requireHost;
  const host = game.objects.get(obj.attachedTo);
  if (host === undefined || host.zone !== Zone.BATTLEFIELD) return false;
  // CR 702.16c-d: protection stops enchanting, equipping, fortifying.
  if (protectedFrom(game, host, obj)) return false;
  for (const ability of chars.abilities) {
    const keyword = abilityKeyword(ability).toLowerCase();
    if (keyword !== "enchant" && keyword !== "equip" && keyword !== "fortify") continue;
    const quality = (ability as Ability).quality;
    if (quality === undefined || quality === null) continue;
    if (isPlayerFilter(quality)) return false; // CR 303.4c: enchant player, on an object
    if ((quality as { $?: string }).$ !== "ObjectFilter") continue;
    if (!matchHooks().matches(game, host, quality as ObjectFilter, { source: obj.id, controller: obj.controller })) return false;
  }
  // CR 301.5, 301.6: part of the type, not the ability's filter.
  const hostChars = characteristicsOf(game, host);
  if (hasSubtype(chars, "Equipment") && !isCreature(hostChars)) return false;
  if (hasSubtype(chars, "Fortification") && !hasType(hostChars, CardType.LAND)) return false;
  return true;
}

/** CR 303.4c for an Aura on a player (and CR 702.16c protection). */
function enchantedPlayerIsLegal(game: Game, obj: GameObject, chars: Characteristics): boolean {
  const playerId = obj.attachedPlayer;
  if (player(game, playerId).hasLost) return false;
  if (!hasSubtype(chars, "Aura")) return false;
  for (const ability of chars.abilities) {
    const quality = (ability as Ability).quality;
    if (abilityKeyword(ability).toLowerCase() !== "enchant" || quality === undefined || quality === null) continue;
    if (!isPlayerFilter(quality)) return false; // enchant [object] can't be on a player
    const opponentsOnly = enchantableScope(quality) === PlayerScope.TARGET_OPPONENT;
    if (opponentsOnly && playerId === obj.controller) return false;
  }
  return !playerProtectedFrom(game, playerId, obj);
}

function hasEligibleProtector(game: Game, obj: GameObject): boolean {
  if (obj.protector === NO_PLAYER) return false;
  return eligibleProtectors(game, obj).includes(obj.protector);
}

/** CR 704.5x acts only on a battle nothing is attacking. */
function isBeingAttacked(game: Game, obj: GameObject): boolean {
  const combat = game.combat as { attackingPermanent?: Map<ObjectId, ObjectId> } | null;
  // PENDING(rules): combat.ts doesn't record what's attacked yet; with no
  // `attackingPermanent` nothing is under attack.
  const attacked = combat?.attackingPermanent;
  if (attacked === undefined) return false;
  for (const target of attacked.values()) if (target === obj.id) return true;
  return false;
}

/**
 * Whether an ability of this permanent has triggered and not left the
 * stack: on it, or waiting to be put on it (CR 704.5v, 714.4, 603.3).
 */
function isSourceOnTheStack(game: Game, obj: GameObject): boolean {
  for (const id of game.stack) {
    const stackObject = game.objects.get(id);
    if (stackObject !== undefined && stackObject.source === obj.id) return true;
  }
  return game.pendingTriggers.some((p) => pendingSource(p) === obj.id);
}

/** CR 714.4: final chapter reached, and no chapter ability waiting. */
function sagaIsFinished(game: Game, obj: GameObject, chars: Characteristics): boolean {
  let final = 0;
  for (const ability of chars.abilities) final = Math.max(final, num((ability as Ability).chapter));
  if (final === 0) return false;
  if (counterCount(obj, "lore") < final) return false;
  return !isSourceOnTheStack(game, obj);
}

// ---------------------------------------------------------------------------
// Objects that cease to exist (CR 704.5d, 704.5e)
// ---------------------------------------------------------------------------

function checkCeased(game: Game, toCease: GameObject[]): void {
  for (const obj of game.objects.values()) {
    // CR 704.5d: a token that has left the battlefield.
    if (obj.kind === ObjectKind.TOKEN && obj.zone !== Zone.BATTLEFIELD) toCease.push(obj);
    // CR 704.5e: a copy of a card outside the stack and the battlefield,
    // save a prepared permanent's copy in exile (CR 722.3c).
    else if (
      obj.kind === ObjectKind.COPY &&
      obj.zone !== Zone.STACK &&
      obj.zone !== Zone.BATTLEFIELD &&
      !keepsItsPrepareCopy(game, obj)
    ) {
      toCease.push(obj);
    }
  }
}

/** CR 722.3c: the copy stays while its permanent is on the battlefield and prepared. */
export function keepsItsPrepareCopy(game: Game, copy: GameObject): boolean {
  if (copy.prepareCopyOf === NO_OBJECT || copy.zone !== Zone.EXILE) return false;
  const permanent = game.objects.get(copy.prepareCopyOf);
  return (
    permanent !== undefined &&
    isLive(permanent) &&
    permanent.zone === Zone.BATTLEFIELD &&
    game.preparedPermanents.has(permanent.id)
  );
}

// ---------------------------------------------------------------------------
// Speed, storied, soulbond (CR 704.5aa, 702.195a, 702.95e)
// ---------------------------------------------------------------------------

/** CR 704.5aa: controlling a start your engines! permanent with no speed gives speed 1. */
function checkSpeed(game: Game, speedStarts: PlayerId[]): void {
  for (const obj of permanents(game)) {
    if (speedStarts.includes(obj.controller)) continue;
    if (player(game, obj.controller).speed) continue;
    if (hasKeyword(characteristicsOf(game, obj), "Start your engines!")) speedStarts.push(obj.controller);
  }
}

/**
 * CR 702.195a: storied. Controlling three or more artifacts, Sagas and/or
 * legendary permanents gives an enduring story for the rest of the game.
 * Not a state-based action, but checked "any time" the same way.
 */
function grantEnduringStories(game: Game): void {
  for (const p of game.players) {
    if (p.hasEnduringStory) continue;
    const mine = permanents(game, p.id);
    if (!mine.some((o) => hasKeyword(characteristicsOf(game, o), "Storied"))) continue;
    let storied = 0;
    for (const o of mine) {
      const c = characteristicsOf(game, o);
      if (hasType(c, CardType.ARTIFACT) || hasSupertype(c, Supertype.LEGENDARY) || hasSubtype(c, "Saga")) storied += 1;
    }
    if (storied >= 3) {
      p.hasEnduringStory = true;
      invalidateAllCharacteristics(game);
      logRecord(game, `${p.name} has an enduring story`, { kind: "designation" });
    }
  }
}

function pairable(game: Game, obj: GameObject | undefined, controller: PlayerId): boolean {
  return (
    obj !== undefined &&
    isLive(obj) &&
    obj.zone === Zone.BATTLEFIELD &&
    obj.controller === controller &&
    isCreature(characteristicsOf(game, obj))
  );
}

/** CR 702.95b, 702.95e: whether this creature is paired now. */
export function isStillPaired(game: Game, obj: GameObject): boolean {
  if (obj.pairedWith === NO_OBJECT) return false;
  const partner = game.objects.get(obj.pairedWith);
  return (
    partner !== undefined &&
    partner.pairedWith === obj.id &&
    pairable(game, obj, obj.pairController) &&
    pairable(game, partner, obj.pairController)
  );
}

/** CR 702.95e: clear every pair that has ended; whether any did. */
export function endBrokenPairs(game: Game): boolean {
  let ended = false;
  for (const id of [...game.battlefield]) {
    const obj = game.objects.get(id);
    if (obj !== undefined && obj.pairedWith !== NO_OBJECT && !isStillPaired(game, obj)) {
      obj.pairedWith = NO_OBJECT;
      ended = true;
    }
  }
  if (ended) invalidateAllCharacteristics(game);
  return ended;
}

// ---------------------------------------------------------------------------
// Legend, world and Role rules (CR 704.5j, 704.5k, 704.5z)
// ---------------------------------------------------------------------------

/** CR 704.5j: one legendary permanent of each name per player. */
function checkLegendRule(game: Game, choices: [PlayerId, string, GameObject[]][]): void {
  const grouped = new Map<string, [PlayerId, string, GameObject[]]>();
  for (const obj of permanents(game)) {
    const chars = characteristicsOf(game, obj);
    if (!hasSupertype(chars, Supertype.LEGENDARY) || !chars.name) continue;
    // PENDING(rules): CR 101.1, a card switching the legend rule off
    // (Python game.rule_is_suspended(Rule.LEGEND_RULE)); cr101_rule_overrides
    // isn't ported, so the rule always applies.
    const key = `${obj.controller}\u0000${chars.name}`;
    const entry = grouped.get(key) ?? [obj.controller, chars.name, []];
    entry[2].push(obj);
    grouped.set(key, entry);
  }
  const ordered = [...grouped.values()].sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  for (const entry of ordered) if (entry[2].length > 1) choices.push(entry);
}

/**
 * Keep one, put the rest in the graveyard. The keeper is the controller's
 * choice; until the AI makes it, the newest is kept, deterministically.
 */
function resolveLegendRule(game: Game, _controller: PlayerId, name: string, group: GameObject[]): void {
  const survivors = [...group].sort((a, b) => a.timestamp - b.timestamp);
  const keep = survivors[survivors.length - 1];
  for (const obj of survivors) {
    if (obj !== keep && obj.zone === Zone.BATTLEFIELD) {
      logRecord(game, `Legend rule: ${name} put into graveyard`, { kind: "sba" });
      putIntoGraveyard(game, obj);
    }
  }
}

/** CR 704.5z: one Role per permanent per player, keeping the newest. */
function checkRoleRule(game: Game, toGraveyard: GameObject[]): void {
  for (const obj of permanents(game)) {
    if (obj.attachments.length < 2) continue;
    const byController = new Map<PlayerId, GameObject[]>();
    for (const id of obj.attachments) {
      const role = game.objects.get(id);
      if (role === undefined || role.zone !== Zone.BATTLEFIELD) continue;
      if (!hasSubtype(characteristicsOf(game, role), "Role")) continue;
      const group = byController.get(role.controller) ?? [];
      group.push(role);
      byController.set(role.controller, group);
    }
    for (const group of byController.values()) {
      if (group.length < 2) continue;
      const ordered = [...group].sort((a, b) => a.timestamp - b.timestamp);
      for (const older of ordered.slice(0, -1)) {
        if (!toGraveyard.includes(older)) {
          logRecord(game, "Role rule: older Role put into graveyard", { kind: "sba" });
          toGraveyard.push(older);
        }
      }
    }
  }
}

/** CR 704.5k: only the newest World permanent survives. */
function checkWorldRule(game: Game, toGraveyard: GameObject[]): void {
  const worlds = permanents(game).filter((o) => hasSupertype(characteristicsOf(game, o), Supertype.WORLD));
  if (worlds.length <= 1) return;
  const newest = Math.max(...worlds.map((o) => o.timestamp));
  for (const obj of worlds) if (obj.timestamp !== newest) toGraveyard.push(obj);
}
