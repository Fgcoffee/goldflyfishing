/**
 * Special actions (CR 116): taken with priority, never using the stack, so
 * they can't be responded to. Ported from
 * mtgfish/rules/cr100_game_concepts/cr116_special_actions.py. Playing a
 * land is in casting.ts. `available` and `perform` must agree: the
 * enumerator is the legality model.
 */

import type { Ability } from "../../ir";
import type { CardDef } from "../../kernel/card";
import { Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { logRecord, player, type Game } from "../../kernel/game";
import { addCounters, objectText, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { hasKeyword } from "../cr200_parts_of_a_card/characteristics";
import { bringIntoGame, cannotBringIn, COMPANION_COST } from "../cr400_zones/outside_game";
import { CastError } from "../cr600_spells_and_abilities/cast_error";
import { characteristicsOf, printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { pay } from "../cr600_spells_and_abilities/paying";
import { cannotTurnFaceUp, manaCostToTurnUp, turnFaceUp as turnUp } from "../cr700_additional_rules/face_down";
import { emit } from "../emit";
import { numSet, str } from "../ir_read";
import { parseCost, variableCount, type ManaCost } from "../mana/cost";
import { canAfford, hasSorcerySpeed } from "../legality";
import { manaComponent, newTotalCost } from "./costs";
import { action, ActionKind, type Action } from "./priority";
import { moveObject } from "../zones";

/** Which special action, by its CR 116.2 letter. */
export const SpecialKind = {
  PLAY_LAND: 0, // 116.2a - in casting.ts
  TURN_FACE_UP: 1, // 116.2b
  END_CONTINUOUS_EFFECT: 2, // 116.2c
  IGNORE_STATIC: 3, // 116.2d
  DISCARD_SELF: 4, // 116.2e
  SUSPEND: 5, // 116.2f
  COMPANION: 6, // 116.2g
  FORETELL: 7, // 116.2h
  ROLL_PLANAR_DIE: 8, // 116.2i - Planechase, out of scope
  UNLOCK_CONSPIRACY: 9, // 116.2j - Conspiracy Draft, out of scope
  PLOT: 10, // 116.2k
  UNLOCK_ROOM: 11, // 116.2m
} as const;
export type SpecialKind = (typeof SpecialKind)[keyof typeof SpecialKind];
const KIND_NAMES = Object.fromEntries(Object.entries(SpecialKind).map(([k, v]) => [v, k])) as Record<number, string>;
export const OUT_OF_SCOPE: ReadonlySet<number> = new Set([SpecialKind.ROLL_PLANAR_DIE, SpecialKind.UNLOCK_CONSPIRACY]);

/** One available special action. */
export interface SpecialAction {
  readonly kind: SpecialKind;
  readonly source: ObjectId;
  readonly cost: ManaCost | null;
  readonly keyword: string;
  readonly option: number;
}

const special = (kind: SpecialKind, fields: Partial<Omit<SpecialAction, "kind">> = {}): SpecialAction => ({
  kind, source: NO_OBJECT, cost: null, keyword: "", option: -1, ...fields,
});

export const asAction = (s: SpecialAction): Action =>
  action(ActionKind.SPECIAL, { source: s.source, abilityIndex: s.kind, alternativeCost: s.option });

/** Every special action this player may take now (land drops are legal actions' PLAY_LAND). */
export function available(game: Game, playerId: PlayerId): SpecialAction[] {
  return [...turnFaceUpOptions(game, playerId), ...fromHand(game, playerId), ...companion(game, playerId)];
}

/** Take a special action; whether anything happened. */
export function perform(game: Game, playerId: PlayerId, act: Action): boolean {
  const kind = act.abilityIndex;
  const obj = game.objects.get(act.source);
  if (kind === SpecialKind.TURN_FACE_UP) return obj !== undefined && payAndTurnFaceUp(game, playerId, obj, act.alternativeCost, act.xValue);
  if (kind === SpecialKind.SUSPEND || kind === SpecialKind.FORETELL || kind === SpecialKind.PLOT) {
    return obj !== undefined && exileFromHand(game, playerId, obj, kind);
  }
  if (kind === SpecialKind.DISCARD_SELF) return obj !== undefined && discardSelf(game, playerId, obj);
  if (kind === SpecialKind.COMPANION) return putCompanionIntoHand(game, playerId);
  if (OUT_OF_SCOPE.has(kind)) {
    logRecord(game, `${KIND_NAMES[kind]} is not supported in this format`, { kind: "unsupported", player: playerId });
    return false;
  }
  return false;
}

// -- turning face up (CR 116.2b, 702.37e, 702.168d, 701.40b) ---------------------------

export const FACE_UP_KEYWORDS: Readonly<Record<string, number>> = { Morph: 0, Megamorph: 1, Disguise: 0 };

function* turnFaceUpOptions(game: Game, playerId: PlayerId): Generator<SpecialAction> {
  for (const id of game.battlefield) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.controller !== playerId || !obj.faceDown) continue;
    const options = faceUpOptions(game, obj);
    for (let option = 0; option < options.length; option += 1) {
      const [keyword, cost] = options[option]!;
      if (canAfford(game, playerId, cost)) yield special(SpecialKind.TURN_FACE_UP, { source: id, cost, keyword, option });
    }
  }
}

/** Every way to turn this face-down permanent up, with its cost (CR 701.40c, 701.58c, 701.40g). */
export function faceUpOptions(game: Game, obj: GameObject): [string, ManaCost][] {
  if (obj.card == null || cannotTurnFaceUp(game, obj)) return [];
  const options = faceUpCosts(game, obj);
  const manaCost = manaCostToTurnUp(game, obj);
  if (manaCost !== null) options.push([obj.faceDownBy, manaCost]);
  return options;
}

/** The morph-family keywords on the face-down permanent's real card (CR 708.4). */
function faceUpCosts(game: Game, obj: GameObject): [string, ManaCost][] {
  if (obj.card == null) return [];
  const out: [string, ManaCost][] = [];
  for (const ability of printedCharacteristics(game, obj).abilities as readonly Ability[]) {
    const keyword = str(ability.keyword);
    if (!(keyword in FACE_UP_KEYWORDS) || ability.alternative_cost != null) continue;
    if (!numSet(ability.functions_in, [Zone.BATTLEFIELD]).has(Zone.BATTLEFIELD)) continue;
    out.push([keyword, manaComponent(ability.cost)]);
  }
  return out;
}

/** Turn a face-down permanent face up (CR 708.8); only a megamorph cost leaves a counter (CR 702.37b). */
export function turnFaceUp(game: Game, obj: GameObject, keyword: string | null = null): boolean {
  const megamorph = keyword === null ? hasKeyword(printedCharacteristics(game, obj), "Megamorph") : keyword === "Megamorph";
  return turnUp(game, obj, { megamorph });
}

function payAndTurnFaceUp(game: Game, playerId: PlayerId, obj: GameObject, option: number, xValue = 0): boolean {
  if (obj.controller !== playerId || !obj.faceDown) return false;
  const options = faceUpOptions(game, obj);
  if (options.length === 0) return false;
  const [keyword, cost] = options[option >= 0 && option < options.length ? option : 0]!;
  try {
    pay(game, playerId, newTotalCost({ base: cost, xValue }), obj);
  } catch (e) {
    if (!(e instanceof CastError)) throw e;
    logRecord(game, `cannot turn ${objectText(obj, characteristicsOf(game, obj).name)} face up: ${e.message}`, { kind: "illegal", player: playerId });
    return false;
  }
  if (variableCount(cost) > 0) obj.xValue = Math.max(0, xValue);
  return turnFaceUp(game, obj, keyword);
}

// -- from hand: suspend, foretell, plot, Circling Vultures --------------------------

/** keyword -> [kind, cost, own turn only, empty stack only] */
const HAND_ACTIONS: readonly [string, SpecialKind, string, boolean, boolean][] = [
  ["Suspend", SpecialKind.SUSPEND, "", false, false],
  ["Foretell", SpecialKind.FORETELL, "{2}", true, false],
  ["Plot", SpecialKind.PLOT, "", true, true],
];

function* fromHand(game: Game, playerId: PlayerId): Generator<SpecialAction> {
  const isOwnTurn = game.activePlayer === playerId;
  const stackEmpty = game.stack.length === 0;
  for (const id of player(game, playerId).hand.slice()) {
    const obj = game.objects.get(id);
    if (obj === undefined) continue;
    const chars = printedCharacteristics(game, obj);
    for (const [keyword, kind, costText, ownTurn, emptyStack] of HAND_ACTIONS) {
      if (!hasKeyword(chars, keyword)) continue;
      if (ownTurn && !isOwnTurn) continue;
      if (emptyStack && !stackEmpty) continue;
      const cost = costText ? parseCost(costText) : null;
      if (!canAfford(game, playerId, cost)) continue;
      yield special(kind, { source: id, cost, keyword });
    }
    if (chars.name === "Circling Vultures") yield special(SpecialKind.DISCARD_SELF, { source: id });
  }
}

/** Suspend, foretell or plot a card from hand: pay, exile, link (CR 607, 702.143a). */
function exileFromHand(game: Game, playerId: PlayerId, obj: GameObject, kind: SpecialKind): boolean {
  const costText = HAND_ACTIONS.find(([, k]) => k === kind)?.[2] ?? "";
  if (costText) {
    try {
      pay(game, playerId, newTotalCost({ base: parseCost(costText) }), obj);
    } catch (e) {
      if (!(e instanceof CastError)) throw e;
      logRecord(game, `cannot ${KIND_NAMES[kind]!.toLowerCase()} ${objectText(obj, characteristicsOf(game, obj).name)}: ${e.message}`, { kind: "illegal", player: playerId });
      return false;
    }
  }
  const faceDown = kind !== SpecialKind.SUSPEND;
  const exiled = moveObject(game, obj, Zone.EXILE, faceDown ? { faceDown: "" } : {});
  exiled.faceDown = faceDown;
  if (!game.exiledWith.has(obj.id)) game.exiledWith.set(obj.id, []);
  logRecord(game, `${objectText(obj, characteristicsOf(game, obj).name)} exiled (${KIND_NAMES[kind]!.toLowerCase()})`, { kind: "special-action", player: playerId });
  if (kind === SpecialKind.SUSPEND) {
    const amount = suspendAmount(game, obj);
    addCounters(exiled, "time", amount);
    emit(game, event(EventKind.COUNTER_ADDED, { objectId: exiled.id, player: playerId, amount, data: ["time"] }));
  }
  return true;
}

function suspendAmount(game: Game, obj: GameObject): number {
  for (const ability of printedCharacteristics(game, obj).abilities as readonly Ability[]) {
    if (str(ability.keyword) === "Suspend") return Math.max(1, (ability.quality as { amount?: number } | null)?.amount || 1);
  }
  return 1;
}

/** CR 116.2e. */
function discardSelf(game: Game, playerId: PlayerId, obj: GameObject): boolean {
  moveObject(game, obj, Zone.GRAVEYARD);
  emit(game, event(EventKind.DISCARDED, { objectId: obj.id, player: playerId }));
  return true;
}

// -- companion (CR 116.2g, 702.139a) -------------------------------------------------

function companionMayComeIn(game: Game, playerId: PlayerId): boolean {
  const p = player(game, playerId);
  if (p.companion == null || p.companionBroughtIn) return false;
  if (!hasSorcerySpeed(game, playerId)) return false;
  return cannotBringIn(game, playerId, p.companion as CardDef) === null;
}

function* companion(game: Game, playerId: PlayerId): Generator<SpecialAction> {
  if (!companionMayComeIn(game, playerId)) return;
  const cost = parseCost(COMPANION_COST);
  if (canAfford(game, playerId, cost)) yield special(SpecialKind.COMPANION, { cost, keyword: "Companion" });
}

function putCompanionIntoHand(game: Game, playerId: PlayerId): boolean {
  if (!companionMayComeIn(game, playerId)) return false;
  const p = player(game, playerId);
  const card = p.companion as CardDef;
  try {
    pay(game, playerId, newTotalCost({ base: parseCost(COMPANION_COST) }), null);
  } catch (e) {
    if (!(e instanceof CastError)) throw e;
    logRecord(game, `cannot put ${card.name} into hand: ${e.message}`, { kind: "illegal", player: playerId });
    return false;
  }
  if (bringIntoGame(game, playerId, card, Zone.HAND) === null) return false;
  p.companionBroughtIn = true;
  logRecord(game, `${p.name} puts their companion ${card.name} into their hand`, { kind: "special-action", player: playerId });
  return true;
}

/** Whether the player has any special action available (CR 724 auto-pass). */
export function canTakeSpecialActions(game: Game, playerId: PlayerId): boolean {
  return available(game, playerId).length > 0;
}

