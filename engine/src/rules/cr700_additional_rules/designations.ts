/**
 * Designations and player-scoped game state (CR 725, 726, 728, 731): the
 * monarch, the initiative, day and night, rad counters. Ported from
 * mtgfish/rules/cr700_additional_rules/cr725_designations.py.
 *
 * Not card abilities: rules the game itself runs. A card says "you become
 * the monarch"; everything that follows from it is the game's job.
 */

import type { Ability, Effect } from "../../ir";
import { event, EventKind, type GameEvent } from "../../kernel/events";
import { apnapOrder, logRecord, player, type Game } from "../../kernel/game";
import { NO_OBJECT, NO_PLAYER, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { ir } from "../ir_read";
import { draw, loseLife, mill } from "../cr100_game_concepts/actions";
import { isLand } from "../cr200_parts_of_a_card/characteristics";
import { printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { pendingTrigger, type PendingTrigger } from "../cr600_spells_and_abilities/triggers";

// ---------------------------------------------------------------------------
// The monarch (CR 725)
// ---------------------------------------------------------------------------

/** Who the monarch is, or NO_PLAYER (CR 725.2: at most one). */
export function monarch(game: Game): PlayerId {
  for (const p of game.players) if (p.isMonarch && !p.hasLost) return p.id;
  return NO_PLAYER;
}

/** Make a player the monarch (CR 725.3), taking the crown from whoever had it (725.2). */
export function becomeMonarch(game: Game, playerId: PlayerId): boolean {
  const p = player(game, playerId);
  if (p.hasLost || p.isMonarch) return false;
  for (const other of game.players) other.isMonarch = other.id === playerId;
  logRecord(game, `${p.name} becomes the monarch`, { kind: "monarch", player: playerId });
  emit(game, event(EventKind.BECAME_MONARCH, { player: playerId }));
  return true;
}

/**
 * CR 725.2: the monarch draws at the beginning of their end step.
 * DIVERGENCE (as in Python): a turn-based action rather than an inherent
 * trigger, so it never uses the stack.
 */
export function monarchEndStepDraw(game: Game): void {
  const holder = monarch(game);
  if (holder !== NO_PLAYER && holder === game.activePlayer) {
    logRecord(game, "The monarch draws a card", { kind: "monarch", player: holder });
    draw(game, holder, 1);
  }
}

/** CR 725.4: combat damage to the monarch takes the crown. */
export function combatDamageToMonarch(game: Game, victim: PlayerId, attackerController: PlayerId): void {
  if (player(game, victim).isMonarch && attackerController !== victim) becomeMonarch(game, attackerController);
}

/** CR 725.5: if the monarch leaves, the active player (or the next) becomes the monarch. */
export function monarchLeftTheGame(game: Game): void {
  if (monarch(game) !== NO_PLAYER) return;
  const order = apnapOrder(game);
  if (order.length) becomeMonarch(game, order[0]!);
}

// ---------------------------------------------------------------------------
// The initiative (CR 726)
// ---------------------------------------------------------------------------

/** CR 726.2: the dungeon the initiative ventures into (CR 701.49d). */
export const UNDERCITY = "Undercity";

export function initiativeHolder(game: Game): PlayerId {
  for (const p of game.players) if (p.hasInitiative && !p.hasLost) return p.id;
  return NO_PLAYER;
}

/** The queued initiative trigger: the triggers port's PendingTrigger. */
export type SourcelessTrigger = PendingTrigger;

/**
 * CR 726.2: one of the initiative's inherent triggered abilities. No
 * source (the initiative is a designation), controlled by whoever had it
 * as it triggered; noted here directly, then resolved like any trigger.
 */
function initiativeTrigger(game: Game, controller: PlayerId, effect: Effect, e: GameEvent, text: string): void {
  if (game.suppressTriggers) return;
  const ability: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    effects: [effect],
    trigger: { $: "TriggerCondition", event_kinds: { $set: [e.kind] }, text },
    text,
  };
  game.pendingTriggers.push(pendingTrigger(NO_OBJECT, ability, e, controller));
}

const ventureIntoUndercity = (): Effect => ({
  $: "Effect",
  kind: ir("EffectKind.VENTURE"),
  players: { $: "PlayerFilter" },
  dungeon_quality: UNDERCITY,
  text: "venture into Undercity",
});

/**
 * CR 726.3: one holder at a time. Returns whether it changed hands; taking
 * it while holding it still counts (CR 726.5), so the venture fires anyway.
 */
export function takeInitiative(game: Game, playerId: PlayerId): boolean {
  const p = player(game, playerId);
  if (p.hasLost) return false;
  const already = p.hasInitiative;
  for (const other of game.players) other.hasInitiative = other.id === playerId;
  logRecord(game, `${p.name} takes the initiative`, { kind: "initiative", player: playerId });
  const e = event(EventKind.TOOK_INITIATIVE, { player: playerId });
  emit(game, e);
  initiativeTrigger(game, playerId, ventureIntoUndercity(), e, "Whenever a player takes the initiative, that player ventures into Undercity.");
  return !already;
}

/** CR 726.2: the holder ventures into Undercity at the beginning of their upkeep. */
export function initiativeUpkeep(game: Game): void {
  const holder = initiativeHolder(game);
  if (holder === NO_PLAYER || holder !== game.activePlayer) return;
  initiativeTrigger(
    game,
    holder,
    ventureIntoUndercity(),
    event(EventKind.UPKEEP, { player: holder }),
    "At the beginning of the upkeep of the player who has the initiative, that player ventures into Undercity.",
  );
}

/**
 * CR 726.2: combat damage to the holder passes the initiative - once per
 * attacking player per damage step. The ability is the holder's.
 */
export function combatDamageToInitiativeHolder(game: Game, victim: PlayerId, attackerControllers: Iterable<PlayerId>): void {
  if (!player(game, victim).hasInitiative) return;
  for (const controller of attackerControllers) {
    initiativeTrigger(
      game,
      victim,
      {
        $: "Effect",
        kind: ir("EffectKind.TAKE_INITIATIVE"),
        players: { $: "PlayerFilter", scope: ir("PlayerScope.SPECIFIC"), specific: controller },
        text: "the controller of those creatures takes the initiative",
      },
      event(EventKind.COMBAT_DAMAGE_DEALT, { player: victim, sourceController: controller }),
      "Whenever one or more creatures a player controls deal combat damage to the player who has the initiative, the controller of those creatures takes the initiative.",
    );
  }
}

/** CR 726.4: if the holder leaves, the active player (or the next) takes it. */
export function initiativeLeftTheGame(game: Game): void {
  if (initiativeHolder(game) !== NO_PLAYER) return;
  const order = apnapOrder(game);
  if (order.length) takeInitiative(game, order[0]!);
}

// ---------------------------------------------------------------------------
// Day and night (CR 731)
// ---------------------------------------------------------------------------

/** True for day, false for night, null when neither (CR 731.1). */
export function isDay(game: Game): boolean | null {
  return game.dayNight;
}

export function becomeDay(game: Game): void {
  if (game.dayNight !== true) {
    game.dayNight = true;
    logRecord(game, "It becomes day", { kind: "daynight" });
    emit(game, event(EventKind.DAY_NIGHT_CHANGED, { amount: 1 }));
  }
}

export function becomeNight(game: Game): void {
  if (game.dayNight !== false) {
    game.dayNight = false;
    logRecord(game, "It becomes night", { kind: "daynight" });
    emit(game, event(EventKind.DAY_NIGHT_CHANGED, { amount: 0 }));
  }
}

/**
 * CR 731.2, as a turn begins: day becomes night if the previous player cast
 * no spells; night becomes day if they cast two or more.
 */
export function checkDayNightTransition(game: Game, previousActive: PlayerId): void {
  if (game.dayNight === null || previousActive === NO_PLAYER) return;
  const cast = game.spellsCastLastTurn;
  if (game.dayNight === true && cast === 0) becomeNight(game);
  else if (game.dayNight === false && cast >= 2) becomeDay(game);
}

// ---------------------------------------------------------------------------
// Rad counters (CR 728)
// ---------------------------------------------------------------------------

/**
 * CR 728.1: mill one per rad counter; for each nonland milled, lose 1 life
 * and remove a rad counter. DIVERGENCE (as in Python): inline, not an
 * inherent trigger.
 */
export function radCounterMilling(game: Game, playerId: PlayerId): void {
  const p = player(game, playerId);
  if (p.rad <= 0) return;
  const before = p.graveyard.length;
  mill(game, playerId, p.rad);
  let nonlands = 0;
  for (const id of p.graveyard.slice(before)) {
    const obj = game.objects.get(id);
    if (obj !== undefined && !isLand(printedCharacteristics(game, obj))) nonlands += 1;
  }
  if (nonlands) {
    loseLife(game, playerId, nonlands);
    p.rad = Math.max(0, p.rad - nonlands);
    logRecord(game, `${p.name} loses ${nonlands} life and ${nonlands} rad counters`, { kind: "rad", player: playerId });
  }
}

export function addRadCounters(game: Game, playerId: PlayerId, amount: number): number {
  if (amount <= 0) return 0;
  player(game, playerId).rad += amount;
  return amount;
}
