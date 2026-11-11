/**
 * CR 700 general terms that the game has to notice as they happen. Ported
 * from mtgfish/rules/cr700_additional_rules/cr700_general.py (`note` and the
 * events it announces); the filters and party size come with matching.
 *
 * Each is announced as its own event, so "if you've committed a crime this
 * turn" or "whenever you expend 4" are ordinary event conditions and triggers.
 */

import { Zone } from "../../kernel/enums";
import { event, EventKind, type GameEvent } from "../../kernel/events";
import { opponents, player, type Game } from "../../kernel/game";
import { ObjectKind } from "../../kernel/gameobject";
import { isPlayerTarget, NO_OBJECT, NO_PLAYER, targetPlayer, type PlayerId } from "../../kernel/ids";
import { isPermanentType } from "../cr200_parts_of_a_card/typeline";
import { printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { emit } from "../emit";

/** The event kinds `note` looks at. */
export const GENERAL_TERM_EVENTS: ReadonlySet<number> = new Set([
  EventKind.ZONE_CHANGE,
  EventKind.CAST_SPELL,
  EventKind.PUT_ON_STACK,
]);

/** Announce the CR 700 happenings an event amounts to. */
export function note(game: Game, e: GameEvent): void {
  if (e.kind === EventKind.ZONE_CHANGE && e.toZone === Zone.GRAVEYARD) {
    descend(game, e);
  } else if (e.kind === EventKind.CAST_SPELL) {
    const spell = game.objects.get(e.objectId);
    if (spell !== undefined) {
      expend(game, e.player, spell.manaSpent);
      crime(game, e.player, spell.targets);
    }
  } else if (e.kind === EventKind.PUT_ON_STACK) {
    const ability = game.objects.get(e.objectId);
    if (ability !== undefined) crime(game, e.player, ability.targets);
  }
}

/** CR 700.11: a permanent card put into its owner's graveyard, from anywhere. */
function descend(game: Game, e: GameEvent): void {
  const obj = game.objects.get(e.objectId);
  if (obj === undefined || obj.kind !== ObjectKind.CARD) return;
  if (!isPermanentType(printedCharacteristics(game, obj).typeLine)) return;
  emit(game, event(EventKind.DESCENDED, { objectId: obj.id, player: obj.owner }));
}

/** CR 700.14: each total of mana spent on spells this turn crossed. */
function expend(game: Game, playerId: PlayerId, spent: number): void {
  if (playerId === NO_PLAYER || spent <= 0) return;
  const p = player(game, playerId);
  const before = p.manaSpentOnSpellsThisTurn;
  p.manaSpentOnSpellsThisTurn = before + spent;
  for (let total = before + 1; total <= before + spent; total += 1) {
    emit(game, event(EventKind.EXPENDED, { player: playerId, amount: total }));
  }
}

/**
 * CR 700.13: targeting an opponent, something an opponent controls, or a
 * card in an opponent's graveyard. Announced once per spell or ability.
 */
function crime(game: Game, playerId: PlayerId, targets: readonly (readonly number[])[]): void {
  if (playerId === NO_PLAYER || targets.length === 0) return;
  const theirs = new Set(opponents(game, playerId));
  for (const chosen of targets) {
    for (const target of chosen) {
      if (isCriminalTarget(game, target, theirs)) {
        emit(game, event(EventKind.CRIME_COMMITTED, { player: playerId }));
        return;
      }
    }
  }
}

function isCriminalTarget(game: Game, target: number, theirs: ReadonlySet<PlayerId>): boolean {
  if (isPlayerTarget(target)) return theirs.has(targetPlayer(target));
  if (target === NO_OBJECT) return false;
  const obj = game.objects.get(target);
  if (obj === undefined) return false;
  if (obj.zone === Zone.GRAVEYARD) return theirs.has(obj.owner);
  if (obj.zone === Zone.BATTLEFIELD || obj.zone === Zone.STACK) return theirs.has(obj.controller);
  return false;
}
