/**
 * Cards outside the game (CR 400.11b, 103.2b, 903.11a; companion). Ported
 * from mtgfish/rules/cr400_zones/cr400_outside_game.py.
 */

import type { CardDef } from "../../kernel/card";
import { Color, Zone } from "../../kernel/enums";
import { createObject, invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { isLive, ObjectKind, type GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { commanderColorIdentity } from "../cr903_commander/color_identity";
import { flag, str } from "../ir_read";

export const COMPANION_COST = "{3}";

/** Whether a card has a companion ability (CR 702.139a), asked of its front face's abilities. */
export function hasCompanionAbility(game: Game, card: CardDef | null): boolean {
  if (card === null) return false;
  return game.abilityProvider.abilitiesFor(card, 0).some((a) => str(a.keyword) === "Companion" && !flag(a.unparsed));
}

/** CR 103.2b: reveal one card with companion from outside the game; whether it was. */
export function revealCompanion(game: Game, playerId: PlayerId, card: CardDef): boolean {
  const p = player(game, playerId);
  if (p.companion != null) return false;
  if (!hasCompanionAbility(game, card)) {
    logRecord(game, `${card.name} has no companion ability and cannot be revealed as a companion`, { kind: "illegal", player: playerId });
    return false;
  }
  if (!p.outsideGame.includes(card)) p.outsideGame.push(card);
  p.companion = card;
  logRecord(game, `${p.name} reveals ${card.name} as their companion`, { kind: "setup", player: playerId });
  return true;
}

function ownedNames(game: Game, playerId: PlayerId): Set<string> {
  const names = new Set<string>();
  for (const obj of game.objects.values()) {
    if (obj.owner !== playerId || obj.kind !== ObjectKind.CARD || !isLive(obj)) continue;
    const name = (obj.card as CardDef | null)?.name ?? "";
    if (name) names.add(name);
  }
  return names;
}

/** Why this card may not come in from outside the game (CR 903.11a), or null. */
export function cannotBringIn(game: Game, playerId: PlayerId, card: CardDef): string | null {
  const p = player(game, playerId);
  const name = card.name ?? "";
  if (!p.outsideGame.includes(card)) return `${name} is not outside the game for ${p.name}`;
  if (p.startingDeckNames.has(name)) return `${name} shares a name with a card in the starting deck`;
  if (ownedNames(game, playerId).has(name)) return `${name} shares a name with a card already in the game`;
  const identity = commanderColorIdentity(game, playerId);
  const cardIdentity = (card.color_identity ?? Color.NONE) as number;
  if (identity !== null && (cardIdentity & ~identity) !== 0) return `${name} is outside the commander's color identity`;
  return null;
}

/** CR 400.11b, 108.3: bring a card in from outside the game; null if CR 903.11a forbids it. */
export function bringIntoGame(game: Game, playerId: PlayerId, card: CardDef, zone: Zone): GameObject | null {
  const reason = cannotBringIn(game, playerId, card);
  if (reason !== null) {
    logRecord(game, `cannot bring in ${card.name}: ${reason}`, { kind: "illegal", player: playerId });
    return null;
  }
  const p = player(game, playerId);
  p.outsideGame.splice(p.outsideGame.indexOf(card), 1);
  const obj = createObject(game, card, playerId, zone);
  invalidateAllCharacteristics(game);
  logRecord(game, `${p.name} brings ${card.name} into the game from outside it`, { kind: "outside-game", player: playerId });
  return obj;
}
