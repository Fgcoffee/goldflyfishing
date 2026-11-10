/**
 * A player and their per-player state (CR 102). Ported from
 * mtgfish/rules/cr100_game_concepts/player.py. Plain data with an explicit
 * deep clone.
 */

import { Zone, lossReasonName, type LossReason } from "../../kernel/enums";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { cloneManaPool, newManaPool, type ManaPool } from "./mana_pool";

export const COMMANDER_STARTING_LIFE = 40;
/** CR 903.10a. */
export const COMMANDER_DAMAGE_THRESHOLD = 21;
/** CR 704.5c. */
export const POISON_THRESHOLD = 10;
export const STARTING_HAND_SIZE = 7;
export const DEFAULT_MAX_HAND_SIZE = 7;
/** CR 702.179: speed tops out at four. */
export const MAX_SPEED = 4;

/** Counters stored as their own field; every other kind is in `counters`. */
const NAMED_PLAYER_COUNTERS = ["poison", "energy", "experience", "rad"] as const;
type NamedCounter = (typeof NAMED_PLAYER_COUNTERS)[number];

function isNamedCounter(kind: string): kind is NamedCounter {
  return (NAMED_PLAYER_COUNTERS as readonly string[]).includes(kind);
}

export interface Player {
  id: PlayerId;
  name: string;
  life: number;
  poison: number;
  energy: number;
  experience: number;
  speed: number;
  speedIncreasedThisTurn: boolean;
  dungeon: ObjectId;
  ventureRoom: number;
  completedDungeons: string[];
  rad: number;
  counters: Map<string, number>;
  library: ObjectId[];
  hand: ObjectId[];
  graveyard: ObjectId[];
  manaPool: ManaPool;
  landsPlayed: number;
  maxLands: number;
  maxHandSize: number;
  cardsDrawnThisTurn: number;
  attemptedDrawFromEmptyLibrary: boolean;
  lifeGainedThisTurn: number;
  lifeLostThisTurn: number;
  commanders: ObjectId[];
  commanderCasts: Map<ObjectId, number>;
  commanderDamage: Map<ObjectId, number>;
  outsideGame: unknown[];
  companion: unknown;
  companionBroughtIn: boolean;
  startingDeckNames: ReadonlySet<string>;
  isMonarch: boolean;
  hasInitiative: boolean;
  hasCityBlessing: boolean;
  ringTemptedCount: number;
  hasEnduringStory: boolean;
  manaSpentOnSpellsThisTurn: number;
  ringBearer: number;
  ringEmblem: number;
  hasLost: boolean;
  hasWon: boolean;
  lossReason: LossReason | null;
  leftOnTurn: number;
  cannotLose: boolean;
  cannotWin: boolean;
}

export function newPlayer(id: PlayerId, fields: Partial<Player> = {}): Player {
  return {
    id,
    name: "",
    life: COMMANDER_STARTING_LIFE,
    poison: 0,
    energy: 0,
    experience: 0,
    speed: 0,
    speedIncreasedThisTurn: false,
    dungeon: NO_OBJECT,
    ventureRoom: 0,
    completedDungeons: [],
    rad: 0,
    counters: new Map(),
    library: [],
    hand: [],
    graveyard: [],
    manaPool: newManaPool(),
    landsPlayed: 0,
    maxLands: 1,
    maxHandSize: DEFAULT_MAX_HAND_SIZE,
    cardsDrawnThisTurn: 0,
    attemptedDrawFromEmptyLibrary: false,
    lifeGainedThisTurn: 0,
    lifeLostThisTurn: 0,
    commanders: [],
    commanderCasts: new Map(),
    commanderDamage: new Map(),
    outsideGame: [],
    companion: null,
    companionBroughtIn: false,
    startingDeckNames: new Set(),
    isMonarch: false,
    hasInitiative: false,
    hasCityBlessing: false,
    ringTemptedCount: 0,
    hasEnduringStory: false,
    manaSpentOnSpellsThisTurn: 0,
    ringBearer: 0,
    ringEmblem: 0,
    hasLost: false,
    hasWon: false,
    lossReason: null,
    leftOnTurn: -1,
    cannotLose: false,
    cannotWin: false,
    ...fields,
  };
}

export function clonePlayer(p: Player): Player {
  return {
    ...p,
    completedDungeons: p.completedDungeons.slice(),
    counters: new Map(p.counters),
    library: p.library.slice(),
    hand: p.hand.slice(),
    graveyard: p.graveyard.slice(),
    manaPool: cloneManaPool(p.manaPool),
    commanders: p.commanders.slice(),
    commanderCasts: new Map(p.commanderCasts),
    commanderDamage: new Map(p.commanderDamage),
    outsideGame: p.outsideGame.slice(),
  };
}

export function atMaxSpeed(p: Player): boolean {
  return p.speed >= MAX_SPEED;
}

/** CR 702.179a: speed becomes 1 if it was 0. Idempotent. */
export function startEngines(p: Player): boolean {
  if (p.speed === 0) {
    p.speed = 1;
    return true;
  }
  return false;
}

/** CR 702.179d: at most once a turn, and not past four. */
export function increaseSpeed(p: Player): boolean {
  if (p.speed === 0 || p.speed >= MAX_SPEED || p.speedIncreasedThisTurn) return false;
  p.speed += 1;
  p.speedIncreasedThisTurn = true;
  return true;
}

/** How many counters of this kind the player has (CR 122.1). */
export function playerCounterCount(p: Player, kind: string): number {
  return isNamedCounter(kind) ? p[kind] : (p.counters.get(kind) ?? 0);
}

export function addPlayerCounters(p: Player, kind: string, amount: number): number {
  if (amount <= 0) return 0;
  if (isNamedCounter(kind)) p[kind] += amount;
  else p.counters.set(kind, (p.counters.get(kind) ?? 0) + amount);
  return amount;
}

export function removePlayerCounters(p: Player, kind: string, amount: number): number {
  const have = playerCounterCount(p, kind);
  const taken = Math.min(have, Math.max(0, amount));
  if (taken === 0) return 0;
  if (isNamedCounter(kind)) p[kind] = have - taken;
  else if (have - taken > 0) p.counters.set(kind, have - taken);
  else p.counters.delete(kind);
  return taken;
}

/** The list backing one of this player's owned zones. */
export function playerZone(p: Player, zone: Zone): ObjectId[] {
  if (zone === Zone.LIBRARY) return p.library;
  if (zone === Zone.HAND) return p.hand;
  if (zone === Zone.GRAVEYARD) return p.graveyard;
  throw new RangeError(`zone ${zone} is not a player-owned zone`);
}

/** CR 119.9, 107.1b: gaining 0 or less is no gain. Returns the amount gained. */
export function gainLife(p: Player, amount: number): number {
  if (amount <= 0) return 0;
  p.life += amount;
  p.lifeGainedThisTurn += amount;
  return amount;
}

/** CR 119.3, 107.1b: a negative loss is zero, not a gain. */
export function loseLife(p: Player, amount: number): number {
  if (amount <= 0) return 0;
  p.life -= amount;
  p.lifeLostThisTurn += amount;
  return amount;
}

/** CR 903.8: {2} per previous cast from the command zone. */
export function commanderTax(p: Player, commander: ObjectId): number {
  return 2 * (p.commanderCasts.get(commander) ?? 0);
}

export function recordCommanderCast(p: Player, commander: ObjectId): void {
  p.commanderCasts.set(commander, (p.commanderCasts.get(commander) ?? 0) + 1);
}

export function takeCommanderDamage(p: Player, commander: ObjectId, amount: number): number {
  if (amount <= 0) return 0;
  const total = (p.commanderDamage.get(commander) ?? 0) + amount;
  p.commanderDamage.set(commander, total);
  return total;
}

/** The commander that has dealt 21+ to this player (CR 903.10a), if any. */
export function lethalCommanderDamage(p: Player): ObjectId {
  for (const [commander, amount] of p.commanderDamage) {
    if (amount >= COMMANDER_DAMAGE_THRESHOLD) return commander;
  }
  return NO_OBJECT;
}

/** Reset per-turn counts, in this player's untap step. */
export function beginPlayerTurn(p: Player): void {
  p.landsPlayed = 0;
  p.cardsDrawnThisTurn = 0;
  p.lifeGainedThisTurn = 0;
  p.lifeLostThisTurn = 0;
  p.manaSpentOnSpellsThisTurn = 0;
}

export function isActiveInGame(p: Player): boolean {
  return !p.hasLost;
}

export function playerText(p: Player): string {
  const status = isActiveInGame(p) ? "" : ` (out: ${lossReasonName(p.lossReason ?? 0)})`;
  return `${p.name || `P${p.id}`}: ${p.life} life${status}`;
}
