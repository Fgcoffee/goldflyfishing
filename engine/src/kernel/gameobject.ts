/**
 * One object in one zone. Ported from mtgfish/rules/kernel/gameobject.py.
 *
 * An object that moves zones becomes a new object (CR 400.7); the old one is
 * kept as a husk for last-known information, marked by `supersededBy`. Plain
 * data throughout, so a game clones by copying (`cloneGameObject`); the
 * characteristics cache is dropped on clone and rebuilt on demand.
 */

import type { Ability } from "../ir";
import type { Characteristics } from "../rules/cr200_parts_of_a_card/characteristics";
import { Zone } from "./enums";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId, type Timestamp } from "./ids";

export const ObjectKind = {
  CARD: 0,
  TOKEN: 1, // CR 111
  ABILITY: 2, // An ability on the stack (CR 113.7)
  EMBLEM: 3, // CR 114
  COPY: 4, // A copy of a spell (CR 707.10)
} as const;
export type ObjectKind = number;

/** The counter key meaning "counters of any kind". */
export const ANY_COUNTER = "*";

export interface GameObject {
  id: ObjectId;
  kind: ObjectKind;
  owner: PlayerId;
  controller: PlayerId;
  baseController: PlayerId;
  zone: Zone;
  /** The card behind it (shape owned by the card data); null for an ability. */
  card: unknown;
  faceIndex: number;
  costPaidObjects: ObjectId[];
  timestamp: Timestamp;
  tapped: boolean;
  flipped: boolean;
  faceDown: boolean;
  faceDownBy: string;
  phasedOut: boolean;
  damage: number;
  dealtDeathtouchDamage: boolean;
  /** Counter kind -> count. A Map, so iteration order is insertion order. */
  counters: Map<string, number>;
  attachedTo: ObjectId;
  /** CR 303.4b: the player this Aura enchants. */
  attachedPlayer: PlayerId;
  attachments: ObjectId[];
  regenerationShields: number;
  goadedBy: Set<PlayerId>;
  protector: PlayerId;
  phasingScheduled: boolean;
  attackedThisTurn: boolean;
  blockedThisTurn: boolean;
  activationsThisTurn: Map<number, number>;
  activationsEver: Map<number, number>;
  previousId: ObjectId;
  supersededBy: ObjectId;
  summoningSick: boolean;
  arrivedSinceLastTurn: boolean;
  /** CR 702.30a: came under its controller's control since their last turn. */
  newlyControlled: boolean;
  enteredBattlefieldTurn: number;
  /** For an ability on the stack: the ability. */
  ability: Ability | null;
  source: ObjectId;
  /** One tuple of chosen targets per targeting instruction. */
  targets: number[][];
  triggerEvent: unknown;
  wasCast: boolean;
  chosenType: string;
  chosenColor: number;
  chosenName: string;
  chosenPlayer: PlayerId;
  castMode: number;
  playableFromHereBy: PlayerId;
  playableFace: number;
  playableFromTurn: number;
  chosenModes: number[];
  xValue: number;
  castWithoutPaying: boolean;
  additionalCostsPaid: number[];
  alternativeCostPaid: string;
  bestowed: boolean;
  castFrom: Zone | null;
  alternativeCostOffered: unknown;
  prepareCopyOf: ObjectId;
  harnessed: boolean;
  saddledTurn: number;
  pairedWith: ObjectId;
  haunting: ObjectId;
  pairController: PlayerId;
  saddledBy: ObjectId[];
  renowned: boolean;
  entersTappedAndAttacking: boolean;
  entersAttackingLike: ObjectId[];
  manaSpent: number;
  manaSpentColors: number;
  phyrexianPaidWithLife: number;
  isCommander: boolean;
  commandPile: string;
  lastKnownCharacteristics: Characteristics | null;
  /** Cache, valid while `characteristicsEpoch` equals the game's epoch. */
  cachedCharacteristics: Characteristics | null;
  characteristicsEpoch: number;
  /** Cache of the printed characteristics, valid while `printedKey` matches. */
  printed: Characteristics | null;
  printedKey: string;
}

export function newGameObject(id: ObjectId, fields: Partial<GameObject> = {}): GameObject {
  return {
    id,
    kind: ObjectKind.CARD,
    owner: NO_PLAYER,
    controller: NO_PLAYER,
    baseController: NO_PLAYER,
    zone: Zone.LIBRARY,
    card: null,
    faceIndex: 0,
    costPaidObjects: [],
    timestamp: 0,
    tapped: false,
    flipped: false,
    faceDown: false,
    faceDownBy: "",
    phasedOut: false,
    damage: 0,
    dealtDeathtouchDamage: false,
    counters: new Map(),
    attachedTo: NO_OBJECT,
    attachedPlayer: NO_PLAYER,
    attachments: [],
    regenerationShields: 0,
    goadedBy: new Set(),
    protector: NO_PLAYER,
    phasingScheduled: false,
    attackedThisTurn: false,
    blockedThisTurn: false,
    activationsThisTurn: new Map(),
    activationsEver: new Map(),
    previousId: NO_OBJECT,
    supersededBy: NO_OBJECT,
    summoningSick: true,
    arrivedSinceLastTurn: false,
    newlyControlled: true,
    enteredBattlefieldTurn: -1,
    ability: null,
    source: NO_OBJECT,
    targets: [],
    triggerEvent: null,
    wasCast: false,
    chosenType: "",
    chosenColor: 0,
    chosenName: "",
    chosenPlayer: NO_PLAYER,
    castMode: 0,
    playableFromHereBy: NO_PLAYER,
    playableFace: 0,
    playableFromTurn: 0,
    chosenModes: [],
    xValue: 0,
    castWithoutPaying: false,
    additionalCostsPaid: [],
    alternativeCostPaid: "",
    bestowed: false,
    castFrom: null,
    alternativeCostOffered: null,
    prepareCopyOf: NO_OBJECT,
    harnessed: false,
    saddledTurn: 0,
    pairedWith: NO_OBJECT,
    haunting: NO_OBJECT,
    pairController: NO_PLAYER,
    saddledBy: [],
    renowned: false,
    entersTappedAndAttacking: false,
    entersAttackingLike: [],
    manaSpent: 0,
    manaSpentColors: 0,
    phyrexianPaidWithLife: 0,
    isCommander: false,
    commandPile: "",
    lastKnownCharacteristics: null,
    cachedCharacteristics: null,
    characteristicsEpoch: -1,
    printed: null,
    printedKey: "",
    ...fields,
  };
}

/**
 * A deep copy for a lookahead's shadow game. Immutable parts (the card, IR
 * abilities, characteristics snapshots, the trigger event) are shared; every
 * mutable container is copied. The cache is dropped.
 */
export function cloneGameObject(obj: GameObject): GameObject {
  return {
    ...obj,
    costPaidObjects: obj.costPaidObjects.slice(),
    counters: new Map(obj.counters),
    attachments: obj.attachments.slice(),
    goadedBy: new Set(obj.goadedBy),
    activationsThisTurn: new Map(obj.activationsThisTurn),
    activationsEver: new Map(obj.activationsEver),
    targets: obj.targets.map((group) => group.slice()),
    chosenModes: obj.chosenModes.slice(),
    additionalCostsPaid: obj.additionalCostsPaid.slice(),
    saddledBy: obj.saddledBy.slice(),
    entersAttackingLike: obj.entersAttackingLike.slice(),
    cachedCharacteristics: null,
    characteristicsEpoch: -1,
    printed: null,
    printedKey: "",
  };
}

/** Still the current object, not a superseded husk. */
export function isLive(obj: GameObject): boolean {
  return obj.supersededBy === NO_OBJECT;
}

/** CR 110.1: a permanent is an object on the battlefield. */
export function isPermanent(obj: GameObject): boolean {
  return isLive(obj) && obj.zone === Zone.BATTLEFIELD;
}

/** CR 112.1, 112.1a: a card or copy on the stack, not an ability. */
export function isSpell(obj: GameObject): boolean {
  return obj.zone === Zone.STACK && obj.kind !== ObjectKind.ABILITY;
}

export function isToken(obj: GameObject): boolean {
  return obj.kind === ObjectKind.TOKEN;
}

export function counterCount(obj: GameObject, kind: string): number {
  if (kind === ANY_COUNTER) {
    let total = 0;
    for (const n of obj.counters.values()) total += n;
    return total;
  }
  return obj.counters.get(kind) ?? 0;
}

/** Add counters, returning how many were added. */
export function addCounters(obj: GameObject, kind: string, amount: number): number {
  if (amount <= 0) return 0;
  obj.counters.set(kind, (obj.counters.get(kind) ?? 0) + amount);
  return amount;
}

/** Remove up to `amount` counters, returning how many came off. */
export function removeCounters(obj: GameObject, kind: string, amount: number): number {
  const have = obj.counters.get(kind) ?? 0;
  const taken = Math.min(have, Math.max(0, amount));
  if (taken > 0) {
    const remaining = have - taken;
    if (remaining > 0) obj.counters.set(kind, remaining);
    else obj.counters.delete(kind);
  }
  return taken;
}

export function enteredThisTurn(obj: GameObject, currentTurn: number): boolean {
  return obj.enteredBattlefieldTurn === currentTurn;
}

export function invalidateCharacteristics(obj: GameObject): void {
  obj.cachedCharacteristics = null;
  obj.characteristicsEpoch = -1;
}

/** "#12 Grizzly Bears (tapped) (1 +1/+1)", for the log. */
export function objectText(obj: GameObject, name: string): string {
  const bits = [`#${obj.id}`, name || "(object)"];
  if (obj.tapped) bits.push("(tapped)");
  if (obj.faceDown) bits.push("(face down)");
  if (obj.phasedOut) bits.push("(phased out)");
  if (obj.damage) bits.push(`(${obj.damage} damage)`);
  if (obj.counters.size > 0) {
    const parts = [...obj.counters.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, n]) => `${n} ${k}`);
    bits.push(`(${parts.join(", ")})`);
  }
  return bits.join(" ");
}
