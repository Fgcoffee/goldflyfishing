/**
 * Attractions (CR 717), and opening and visiting them (CR 701.51, 701.52).
 * Ported from mtgfish/rules/cr700_additional_rules/cr717_attractions.py.
 *
 * An Attraction never starts in the library: CR 717.2 gives its owner an
 * Attraction deck in the command zone. Opening moves its top card onto the
 * battlefield (CR 701.51b); rolling to visit rolls a d6 and visits each
 * Attraction its roller controls with that number lit up (CR 701.52a).
 *
 * Neither the deck nor the junkyard is a zone (CR 717.2, 717.6a): both are
 * piles of command-zone objects, told apart by `commandPile`, the deck
 * ordered top first as the command zone holds it.
 *
 * The lit-up numbers are printed on the card (CR 717.1), read from the card
 * data's `attraction_lights`; a card with none is never visited.
 */

import type { CardDef } from "../../kernel/card";
import { Zone } from "../../kernel/enums";
import { event, EventKind, replaced, type GameEvent } from "../../kernel/events";
import { createObject, logRecord, permanents, player, type Game } from "../../kernel/game";
import { ObjectKind, type GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { shuffle } from "../../kernel/random";
import { emit } from "../emit";
import { strs } from "../ir_read";
import { hasSubtype } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { rollDie } from "../cr600_spells_and_abilities/resolve";
import { moveObject } from "../zones";

/** CR 717.2: the pile a player's face-down Attraction deck is kept in. */
export const ATTRACTION_DECK = "attraction deck";
/** CR 717.6a: the face-up pile of Attractions that went to the command zone instead. */
export const JUNKYARD = "junkyard";
/** CR 701.52a: the die rolled to visit is six-sided. */
export const VISIT_DIE_SIDES = 6;
/** CR 717.6: where an Astrotorium-backed card may go as itself. */
const ASTROTORIUM_ZONES: ReadonlySet<Zone> = new Set([Zone.BATTLEFIELD, Zone.EXILE, Zone.COMMAND]);

/**
 * CR 717.1: whether this is an Attraction card, with its card back - read
 * from the printed card, not current characteristics. Tokens and copies
 * have no back.
 */
export function hasAstrotoriumBack(obj: GameObject): boolean {
  if (obj.kind !== ObjectKind.CARD || obj.card == null) return false;
  const front = (obj.card as Partial<CardDef>).faces?.[0];
  return front !== undefined && strs(front.type_line.subtypes).includes("Attraction");
}

/** CR 717.1: the numbers lit up on this Attraction's card. */
export function litNumbers(obj: GameObject): ReadonlySet<number> {
  return new Set(((obj.card as { attraction_lights?: readonly number[] } | null)?.attraction_lights ?? []) as number[]);
}

function pile(game: Game, playerId: PlayerId, name: string): GameObject[] {
  const out: GameObject[] = [];
  for (const id of game.command) {
    const obj = game.objects.get(id);
    if (obj !== undefined && obj.owner === playerId && obj.commandPile === name) out.push(obj);
  }
  return out;
}

/** CR 717.2: a player's Attraction deck, top card first. */
export const attractionDeck = (game: Game, playerId: PlayerId): GameObject[] => pile(game, playerId, ATTRACTION_DECK);
/** CR 717.6a: a player's junkyard. */
export const junkyard = (game: Game, playerId: PlayerId): GameObject[] => pile(game, playerId, JUNKYARD);

/** CR 717.2: the Attraction deck begins the game in the command zone. */
export function setUpAttractionDeck(game: Game, playerId: PlayerId, cards: Iterable<unknown>): void {
  for (const card of cards) createObject(game, card, playerId, Zone.COMMAND).commandPile = ATTRACTION_DECK;
}

/** CR 103.3a: a supplementary deck is shuffled by its owner (only its order among itself matters). */
export function shuffleAttractionDeck(game: Game, playerId: PlayerId): void {
  const ids = attractionDeck(game, playerId).map((o) => o.id);
  if (ids.length === 0) return;
  for (const id of ids) game.command.splice(game.command.indexOf(id), 1);
  shuffle(game.rng, ids);
  game.command.push(...ids);
}

/** Whether a player controls one or more Attractions (CR 505.5). */
export function controlsAnAttraction(game: Game, playerId: PlayerId): boolean {
  return permanents(game, playerId).some((o) => hasSubtype(characteristicsOf(game, o), "Attraction"));
}

/**
 * CR 717.6: an Astrotorium-backed card bound anywhere but the battlefield,
 * exile or the command zone goes to the command zone instead - after every
 * other replacement, whatever they produced.
 */
export function junkyardReplacement(game: Game, obj: GameObject, e: GameEvent): GameEvent {
  const destination = e.toZone;
  if (destination === null || ASTROTORIUM_ZONES.has(destination)) return e;
  if (!hasAstrotoriumBack(obj)) return e;
  logRecord(game, `${characteristicsOf(game, obj).name} goes to its owner's junkyard instead`, { kind: "attraction", player: obj.owner });
  return replaced(e, { toZone: Zone.COMMAND });
}

/**
 * CR 717.6a: an Astrotorium-backed card put into the command zone this way
 * joins the junkyard (the deck is only built at setup). For zones.ts.
 */
export function arrivedInCommandZone(obj: GameObject): void {
  if (hasAstrotoriumBack(obj)) obj.commandPile = JUNKYARD;
}

/**
 * CR 701.51b: the top card of your Attraction deck onto the battlefield.
 * Null with nothing to open, or if it never arrived (CR 701.51c).
 */
export function openAttraction(game: Game, playerId: PlayerId): GameObject | null {
  const top = attractionDeck(game, playerId)[0];
  if (top === undefined) return null;
  const opened = moveObject(game, top, Zone.BATTLEFIELD, { toPlayer: playerId });
  if (opened === top || opened.zone !== Zone.BATTLEFIELD) return null;
  opened.commandPile = "";
  logRecord(game, `${player(game, playerId).name} opens ${characteristicsOf(game, opened).name}`, { kind: "attraction", player: playerId });
  emit(game, event(EventKind.ATTRACTION_OPENED, { objectId: opened.id, player: playerId }));
  return opened;
}

/**
 * CR 701.52a: roll a d6, then visit each Attraction you control with that
 * number lit up; each visit is its own event (CR 702.159a). Returns the roll.
 */
export function rollToVisit(game: Game, playerId: PlayerId): number {
  const result = rollDie(game, playerId, VISIT_DIE_SIDES);
  emit(game, event(EventKind.ROLLED_TO_VISIT, { player: playerId, amount: result }));
  const visited = permanents(game, playerId).filter((o) => hasSubtype(characteristicsOf(game, o), "Attraction") && litNumbers(o).has(result));
  for (const obj of visited) {
    logRecord(game, `${player(game, playerId).name} visits ${characteristicsOf(game, obj).name}`, { kind: "attraction", player: playerId });
    emit(game, event(EventKind.ATTRACTION_VISITED, { objectId: obj.id, player: playerId, amount: result }));
  }
  return result;
}
