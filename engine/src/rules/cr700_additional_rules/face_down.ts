/**
 * Face-down permanents (CR 708, 701.40, 701.58). Ported from
 * mtgfish/rules/cr700_additional_rules/cr708_face_down.py: only what special
 * actions needs (mana_cost_to_turn_up, cannot_turn_face_up, turn_face_up,
 * reveal), written by the rules session; the rest (manifest, cloak,
 * manifest dread, turning face down, who may look) by the parser session.
 * The face-down characteristics themselves are in layers.ts.
 */

import { CardType, Layout, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, invalidateAllCharacteristics, logRecord, permanents, player, type Game } from "../../kernel/game";
import { addCounters, invalidateCharacteristics, isLive, isPermanent, objectText, type GameObject } from "../../kernel/gameobject";
import { allocateTimestamp, type PlayerId } from "../../kernel/ids";
import { hasType } from "../cr200_parts_of_a_card/characteristics";
import { printedCharacteristics, characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { emit } from "../emit";
import type { Characteristics } from "../cr200_parts_of_a_card/characteristics";
import { layoutOf } from "./faces";
import { moveObject } from "../zones";
import { fromIR, type ManaCost } from "../mana/cost";

/** Face-down by these can be turned up for the card's mana cost (CR 701.40b, 701.58b). */
export const TURNED_UP_FOR_MANA_COST: ReadonlySet<string> = new Set(["Manifest", "Cloak"]);

/** The mana cost to turn a manifested or cloaked creature card up, or null. */
export function manaCostToTurnUp(game: Game, obj: GameObject): ManaCost | null {
  if (!obj.faceDown || !TURNED_UP_FOR_MANA_COST.has(obj.faceDownBy)) return null;
  const card = printedCharacteristics(game, obj);
  if (!hasType(card, CardType.CREATURE) || !card.hasManaCost) return null;
  return fromIR(card.manaCost);
}

/** CR 701.40g, 701.58g: an instant or sorcery card stays face down. */
export function cannotTurnFaceUp(game: Game, obj: GameObject): boolean {
  return (printedCharacteristics(game, obj).typeLine.types & (CardType.INSTANT | CardType.SORCERY)) !== 0;
}

/** Show every player the card under a face-down object (CR 708.9). */
export function reveal(game: Game, obj: GameObject, why: string): void {
  const name = obj.card != null ? printedCharacteristics(game, obj).name : "";
  logRecord(game, `face-down #${obj.id} is revealed: ${name} (${why})`, { kind: "reveal", player: obj.owner });
}

/**
 * Turn a face-down permanent face up (CR 708.8): the same object, a new
 * timestamp (CR 613.7f), nothing about entering. A megamorph cost leaves a
 * +1/+1 counter (CR 702.37b).
 */
export function turnFaceUp(game: Game, obj: GameObject, options: { megamorph?: boolean } = {}): boolean {
  if (!obj.faceDown || obj.zone !== Zone.BATTLEFIELD) return false;
  if (cannotTurnFaceUp(game, obj)) {
    reveal(game, obj, "it can't be turned face up");
    return false;
  }
  obj.faceDown = false;
  obj.faceDownBy = "";
  obj.timestamp = allocateTimestamp(game.ids);
  invalidateCharacteristics(obj);
  invalidateAllCharacteristics(game);
  // PORT: CR 208.2b "as this enters or is turned face up" choices
  // (_as_it_is_turned_face_up) come with replacement effects.
  if (options.megamorph) {
    addCounters(obj, "+1/+1", 1);
    invalidateCharacteristics(obj);
    invalidateAllCharacteristics(game);
  }
  logRecord(game, `${objectText(obj, characteristicsOf(game, obj).name)} is turned face up`, { kind: "face-up", player: obj.controller });
  emit(game, event(EventKind.TURNED_FACE_UP, { objectId: obj.id, player: obj.controller }));
  return true;
}

// ---------------------------------------------------------------------------
// Manifest, cloak, manifest dread, turning face down (parser session)
// ---------------------------------------------------------------------------

/** Face-down by these have ward {2} (CR 702.168a, 701.58a). */
export const WARDED: ReadonlySet<string> = new Set(["Disguise", "Cloak"]);

const FACE_DOWN_BODY_ZONES: ReadonlySet<Zone> = new Set([Zone.BATTLEFIELD, Zone.STACK]);

/** A face-down card outside the battlefield and the stack has no characteristics (CR 406.3a). */
export function isHidden(obj: GameObject): boolean {
  return obj.faceDown && !FACE_DOWN_BODY_ZONES.has(obj.zone);
}

/**
 * CR 701.40a / 701.58a: manifest (or cloak) one card. It's turned face
 * down before it enters (CR 708.3), so its own enters abilities neither
 * trigger nor apply. CR 701.40f: if it may not enter, it stays. Returns the
 * permanent, or null.
 */
export function manifest(game: Game, playerId: PlayerId, card: GameObject, how = "Manifest"): GameObject | null {
  if (!isLive(card) || card.zone === Zone.BATTLEFIELD) return null;
  const permanent = moveObject(game, card, Zone.BATTLEFIELD, { toPlayer: playerId, faceDown: how });
  if (permanent === card || !isPermanent(permanent)) return null;
  permanent.controller = playerId;
  logRecord(game, `${how.toLowerCase()}s a card`, { kind: "face-down", player: playerId });
  return permanent;
}

interface DreadChooser {
  chooseManifestDread?(game: Game, playerId: PlayerId, cards: GameObject[]): GameObject | null;
}

/**
 * CR 701.62a: look at the top two, manifest one, the rest to the
 * graveyard. The agent's `chooseManifestDread`; without one a creature card
 * (only those turn up for their mana cost, CR 701.40b). CR 701.62b: the
 * "whenever you manifest dread" event comes after the whole process.
 */
export function manifestDread(game: Game, playerId: PlayerId): GameObject | null {
  const looked = player(game, playerId)
    .library.slice(0, 2)
    .map((id) => game.objects.get(id))
    .filter((o): o is GameObject => o !== undefined);
  let manifested: GameObject | null = null;
  if (looked.length) {
    const agent = agentFor(game, playerId) as DreadChooser | null | undefined;
    let chosen = agent && typeof agent.chooseManifestDread === "function" ? agent.chooseManifestDread(game, playerId, [...looked]) : null;
    if (chosen === null || !looked.includes(chosen)) {
      chosen = looked.find((c) => hasType(printedCharacteristics(game, c), CardType.CREATURE)) ?? looked[0]!;
    }
    manifested = manifest(game, playerId, chosen);
    for (const card of looked) if (card !== chosen && isLive(card)) moveObject(game, card, Zone.GRAVEYARD, { toPlayer: card.owner });
  }
  emit(game, event(EventKind.MANIFESTED_DREAD, { objectId: manifested?.id ?? 0, player: playerId }));
  return manifested;
}

/**
 * Turn a face-up permanent face down (CR 708.2a): a plain 2/2, with a new
 * timestamp (CR 613.7f). One already face down stays as it is (708.2b); a
 * double-faced one can't be (712.16).
 */
export function turnFaceDown(game: Game, obj: GameObject): boolean {
  if (obj.faceDown || obj.zone !== Zone.BATTLEFIELD) return false;
  const layout = layoutOf(obj);
  if (layout === Layout.TRANSFORM || layout === Layout.MODAL_DFC || layout === Layout.MELD) return false;
  obj.faceDown = true;
  obj.faceDownBy = "";
  obj.timestamp = allocateTimestamp(game.ids);
  invalidateCharacteristics(obj);
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.TURNED_FACE_DOWN, { objectId: obj.id, player: obj.controller }));
  return true;
}

/**
 * CR 708.5: whether this player may see what a face-down object is - its
 * controller, for a face-down spell or permanent; nobody, elsewhere.
 */
export function mayLookAt(_game: Game, viewer: PlayerId, obj: GameObject): boolean {
  if (!obj.faceDown) return true;
  if (!FACE_DOWN_BODY_ZONES.has(obj.zone)) return false;
  return obj.controller === viewer;
}

/** What a player may know of an object's characteristics (CR 708.5) - for bots and the UI. */
export function characteristicsSeenBy(game: Game, viewer: PlayerId, obj: GameObject): Characteristics {
  if (mayLookAt(game, viewer, obj) && obj.faceDown && obj.card != null) return printedCharacteristics(game, obj);
  return characteristicsOf(game, obj);
}

/**
 * CR 708.6: a player's face-down spells and permanents, told apart: spells
 * in cast order, then permanents in the order they entered (timestamps).
 */
export function faceDownOrder(game: Game, controller: PlayerId): GameObject[] {
  const stack = game.stack.map((id) => game.objects.get(id)).filter((o): o is GameObject => o !== undefined);
  return [...stack, ...permanents(game, controller)]
    .filter((o) => o.faceDown && o.controller === controller)
    .sort((a, b) => Number(a.zone !== Zone.STACK) - Number(b.zone !== Zone.STACK) || a.timestamp - b.timestamp || a.id - b.id);
}

/** CR 708.9: reveal every face-down spell and permanent - a leaving player's, or all at game end. */
export function revealAll(game: Game, owner: PlayerId | null = null): void {
  for (const id of [...game.battlefield, ...game.stack]) {
    const obj = game.objects.get(id);
    if (obj === undefined || !obj.faceDown) continue;
    if (owner !== null && obj.owner !== owner) continue;
    reveal(game, obj, owner === null ? "the game ended" : "its owner left");
  }
}
