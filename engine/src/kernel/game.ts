/**
 * The state of one game. Ported from the state and basic queries of
 * mtgfish/rules/kernel/game.py; moving objects between zones, characteristics
 * and the event bus come with the core rules (they need replacements,
 * triggers and layers).
 *
 * Plain data with an explicit `cloneGame`: the AI's lookahead plays a move out
 * on a clone, and Python did that with a pickle round trip. The clone copies
 * the PRNG state, so planning never advances the real game's stream.
 *
 * Fields owned by modules not ported yet are typed loosely (`unknown[]`,
 * `Map<..., unknown>`); each is tightened when its module lands, and its
 * entry in `cloneGame` with it.
 */

import type { Ability } from "../ir";
import type { Characteristics } from "../rules/cr200_parts_of_a_card/characteristics";
import {
  clonePlayer,
  type Player,
} from "../rules/cr100_game_concepts/player";
import { Phase, Step, Zone } from "./enums";
import { cloneGameObject, newGameObject, ObjectKind, type GameObject } from "./gameobject";
import {
  allocateObjectId,
  allocateTimestamp,
  cloneIdAllocator,
  newIdAllocator,
  NO_OBJECT,
  NO_PLAYER,
  type IdAllocator,
  type ObjectId,
  type PlayerId,
} from "./ids";
import type { GameEvent } from "./events";
import { cloneGameLog, newGameLog, record, type GameLog } from "./log";
import { cloneRng, newRng, type Rng } from "./random";
import { STRICT, type Relaxations } from "./relaxations";

/** Where a card's abilities come from: the IR, keyed by card and face. */
export interface AbilityProvider {
  abilitiesFor(card: unknown, faceIndex: number): readonly Ability[];
  /** Bumped when the provider's answers change (tests rewriting a card). */
  readonly generation?: number;
}

/** A provider that knows no abilities: every card reads as blank. */
export const NO_ABILITIES: AbilityProvider = { abilitiesFor: () => [] };

/**
 * One active continuous effect (CR 611), ordered by timestamp within its
 * layer (CR 613.7). `effect` is the IR effect, or an engine-built one.
 */
export interface ContinuousEffect {
  effect: unknown;
  source: ObjectId;
  controller: PlayerId;
  timestamp: number;
  layer: number;
  duration: number;
  createdTurn: number;
  ability: Ability | null;
  fromStaticAbility: boolean;
  isCda: boolean;
  expired: boolean;
}

export interface Game {
  players: Player[];
  objects: Map<ObjectId, GameObject>;
  battlefield: ObjectId[];
  stack: ObjectId[];
  exile: ObjectId[];
  command: ObjectId[];
  ids: IdAllocator;
  rng: Rng;
  log: GameLog;
  abilityProvider: AbilityProvider;
  /** Rules the bench switched off; STRICT in every real run. */
  relaxations: Relaxations;
  turn: number;
  turnOrder: PlayerId[];
  activePlayer: PlayerId;
  priorityPlayer: PlayerId;
  phase: Phase;
  step: Step;
  continuousEffects: ContinuousEffect[];
  pendingTriggers: unknown[];
  delayedTriggers: unknown[];
  /** Bumped whenever characteristics may have changed; caches compare to it. */
  epoch: number;
  /** The whole-board layer computation, valid while `boardEpoch === epoch`. */
  boardCache: Map<ObjectId, Characteristics> | null;
  boardEpoch: number;
  /** The board being computed right now, for reads made during it. */
  boardInProgress: Map<ObjectId, Characteristics> | null;
  gameOver: boolean;
  winners: PlayerId[];
  commanderOrigin: Map<ObjectId, ObjectId>;
  replacementEffects: unknown[];
  actionsTaken: number;
  runaway: string;
  /**
   * This turn's tally of events, for "if X happened this turn" (Python
   * `turn_history`). Keys: `turnHistoryKey(kind, player)` -> total amount
   * (at least 1 per event), and `turnHistoryCountKey(kind, player)` -> how
   * many events.
   */
  turnHistory: Map<string, number>;
  /** The simulator's statistics callback, given every event. */
  observer: ((game: Game, event: GameEvent) => void) | null;
  eventBatch: number;
  departedInBatch: ObjectId[];
  departedBatch: number;
  resolvingSpell: ObjectId;
  triggeredOnceThisTurn: Set<string>;
  standingPermissions: unknown[];
  standingRestrictions: unknown[];
  standingRequirements: unknown[];
  ruleOverrides: unknown[];
  /** CR 607: source id -> [object id, link id] it exiled or put somewhere. */
  exiledWith: Map<ObjectId, [ObjectId, number][]>;
  combat: unknown;
  commandersAwaitingZoneChoice: ObjectId[];
  dayNight: boolean | null;
  spellsCastThisTurn: number;
  spellsCastLastTurn: number;
  endTurnRequested: boolean;
  skippedSteps: Set<Step>;
  extraTurns: PlayerId[];
  /** CR 500.8, 500.9: queued extra phases and steps; the latest goes first. */
  extraPhases: Phase[];
  extraSteps: Step[];
  /** CR 727: a restart was requested, and who goes first in the new game. */
  restartRequested: boolean;
  restartStartingPlayer: PlayerId | null;
  /** The decks the game was built from, for a restart (CR 727.2). */
  sourceDecks: unknown[];
  /** Loop-detection state (rules/loops), cleared each turn. */
  exhaustedLoopSteps: Set<string>;
  continuingLoopSteps: Set<string>;
  /** Caches over the standing restrictions and permissions. */
  restrictionsCache: unknown[] | null;
  permissionsEpoch: number;
  controlledPlayers: Map<PlayerId, PlayerId>;
  preparedPermanents: Set<ObjectId>;
  classLevels: Map<ObjectId, number>;
  solvedPermanents: Set<ObjectId>;
  /** Who decides for each seat. Not game state: a clone shares them. */
  agents: Map<PlayerId, unknown>;
  lastLoopTurn: number;
  loopDraw: boolean;
  suppressTriggers: boolean;
}

export interface NewGameOptions {
  seed?: number;
  logEnabled?: boolean;
  abilityProvider?: AbilityProvider;
  relaxations?: Relaxations;
}

export function newGame(players: Player[], options: NewGameOptions = {}): Game {
  return {
    players,
    objects: new Map(),
    battlefield: [],
    stack: [],
    exile: [],
    command: [],
    ids: newIdAllocator(),
    rng: newRng(options.seed ?? 0),
    log: newGameLog(options.logEnabled ?? false),
    abilityProvider: options.abilityProvider ?? NO_ABILITIES,
    relaxations: options.relaxations ?? STRICT,
    turn: 0,
    turnOrder: players.map((p) => p.id),
    activePlayer: NO_PLAYER,
    priorityPlayer: NO_PLAYER,
    phase: Phase.BEGINNING,
    step: Step.UNTAP,
    continuousEffects: [],
    pendingTriggers: [],
    delayedTriggers: [],
    epoch: 0,
    boardCache: null,
    boardEpoch: -1,
    boardInProgress: null,
    gameOver: false,
    winners: [],
    commanderOrigin: new Map(),
    replacementEffects: [],
    actionsTaken: 0,
    runaway: "",
    turnHistory: new Map(),
    observer: null,
    eventBatch: 0,
    departedInBatch: [],
    departedBatch: -1,
    resolvingSpell: NO_OBJECT,
    triggeredOnceThisTurn: new Set(),
    standingPermissions: [],
    standingRestrictions: [],
    standingRequirements: [],
    ruleOverrides: [],
    exiledWith: new Map(),
    combat: null,
    commandersAwaitingZoneChoice: [],
    dayNight: null,
    spellsCastThisTurn: 0,
    spellsCastLastTurn: 0,
    endTurnRequested: false,
    skippedSteps: new Set(),
    extraTurns: [],
    extraPhases: [],
    extraSteps: [],
    restartRequested: false,
    restartStartingPlayer: null,
    sourceDecks: [],
    exhaustedLoopSteps: new Set(),
    continuingLoopSteps: new Set(),
    restrictionsCache: null,
    permissionsEpoch: -1,
    controlledPlayers: new Map(),
    preparedPermanents: new Set(),
    classLevels: new Map(),
    solvedPermanents: new Set(),
    agents: new Map(),
    lastLoopTurn: -1,
    loopDraw: false,
    suppressTriggers: false,
  };
}

function cloneRecords<T>(items: readonly T[]): T[] {
  return items.map((x) => (typeof x === "object" && x !== null ? { ...x } : x));
}

/**
 * A copy the lookahead can play out without touching this game. Every
 * mutable container is copied; immutable parts (cards, IR abilities,
 * characteristics snapshots) are shared. Agents and the ability provider are
 * shared on purpose: they are the players and the card data, not the board.
 */
export function cloneGame(game: Game): Game {
  const objects = new Map<ObjectId, GameObject>();
  for (const [id, obj] of game.objects) objects.set(id, cloneGameObject(obj));
  const exiledWith = new Map<ObjectId, [ObjectId, number][]>();
  for (const [id, links] of game.exiledWith) exiledWith.set(id, links.map(([o, l]) => [o, l]));
  return {
    ...game,
    players: game.players.map(clonePlayer),
    objects,
    battlefield: game.battlefield.slice(),
    stack: game.stack.slice(),
    exile: game.exile.slice(),
    command: game.command.slice(),
    ids: cloneIdAllocator(game.ids),
    rng: cloneRng(game.rng),
    log: cloneGameLog(game.log),
    boardCache: null,
    boardEpoch: -1,
    boardInProgress: null,
    turnOrder: game.turnOrder.slice(),
    continuousEffects: cloneRecords(game.continuousEffects),
    pendingTriggers: cloneRecords(game.pendingTriggers),
    delayedTriggers: cloneRecords(game.delayedTriggers),
    winners: game.winners.slice(),
    commanderOrigin: new Map(game.commanderOrigin),
    replacementEffects: cloneRecords(game.replacementEffects),
    turnHistory: new Map(game.turnHistory),
    departedInBatch: game.departedInBatch.slice(),
    triggeredOnceThisTurn: new Set(game.triggeredOnceThisTurn),
    standingPermissions: cloneRecords(game.standingPermissions),
    standingRestrictions: cloneRecords(game.standingRestrictions),
    standingRequirements: cloneRecords(game.standingRequirements),
    ruleOverrides: cloneRecords(game.ruleOverrides),
    exiledWith,
    combat: game.combat === null ? null : structuredClone(game.combat),
    commandersAwaitingZoneChoice: game.commandersAwaitingZoneChoice.slice(),
    skippedSteps: new Set(game.skippedSteps),
    extraTurns: game.extraTurns.slice(),
    extraPhases: game.extraPhases.slice(),
    extraSteps: game.extraSteps.slice(),
    sourceDecks: game.sourceDecks.slice(),
    exhaustedLoopSteps: new Set(game.exhaustedLoopSteps),
    continuingLoopSteps: new Set(game.continuingLoopSteps),
    restrictionsCache: null,
    permissionsEpoch: -1,
    controlledPlayers: new Map(game.controlledPlayers),
    preparedPermanents: new Set(game.preparedPermanents),
    classLevels: new Map(game.classLevels),
    solvedPermanents: new Set(game.solvedPermanents),
  };
}

export function turnHistoryKey(kind: number, player: PlayerId): string {
  return `${kind}|${player}`;
}

export function turnHistoryCountKey(kind: number, player: PlayerId): string {
  return `${kind}|${player}|count`;
}

/** Write a log entry stamped with the game's current time. */
export function logRecord(
  game: Game,
  text: string,
  options: { kind?: string; player?: PlayerId } = {},
): void {
  record(game.log, game, text, options);
}

/** Mark every cached characteristic stale. */
export function invalidateAllCharacteristics(game: Game): void {
  game.epoch += 1;
}

export function player(game: Game, playerId: PlayerId): Player {
  const p = game.players[playerId];
  if (p === undefined) throw new RangeError(`no player ${playerId}`);
  return p;
}

export function getObject(game: Game, id: ObjectId): GameObject | undefined {
  return game.objects.get(id);
}

export function livingPlayers(game: Game): Player[] {
  return game.players.filter((p) => !p.hasLost);
}

export function opponents(game: Game, playerId: PlayerId): PlayerId[] {
  return game.players.filter((p) => p.id !== playerId && !p.hasLost).map((p) => p.id);
}

/**
 * Living players in turn order from `start`. Players who left keep their seat
 * but are skipped, so the survivors' order never changes (CR 800.4).
 */
export function turnOrderFrom(game: Game, start: PlayerId): PlayerId[] {
  if (game.turnOrder.length === 0) return [];
  let offset = game.turnOrder.indexOf(start);
  if (offset < 0) offset = 0;
  const rotated = [...game.turnOrder.slice(offset), ...game.turnOrder.slice(0, offset)];
  return rotated.filter((p) => !player(game, p).hasLost);
}

/** Active player, then non-active players in turn order (CR 101.4). */
export function apnapOrder(game: Game): PlayerId[] {
  return turnOrderFrom(game, game.activePlayer);
}

export function nextPlayer(game: Game, after: PlayerId): PlayerId {
  const order = turnOrderFrom(game, after);
  return order.length > 1 ? (order[1] as PlayerId) : after;
}

/** CR 723.1: whether someone else is making this player's decisions. */
export function isControlled(game: Game, playerId: PlayerId): boolean {
  const controller = game.controlledPlayers.get(playerId);
  return controller !== undefined && controller !== playerId;
}

/**
 * Whoever decides for this player now. CR 723.5: a controlled player's
 * choices are made by their controller; only the decisions move (CR 723.3).
 */
export function agentFor(game: Game, playerId: PlayerId): unknown {
  const controller = game.controlledPlayers.get(playerId);
  if (controller !== undefined && controller !== playerId) return game.agents.get(controller);
  return game.agents.get(playerId);
}

export function controlPlayer(game: Game, controlled: PlayerId, controller: PlayerId): void {
  game.controlledPlayers.set(controlled, controller);
  logRecord(game, `${player(game, controller).name} controls ${player(game, controlled).name}`, {
    kind: "control-player",
    player: controller,
  });
}

export function releasePlayer(game: Game, controlled: PlayerId): void {
  if (game.controlledPlayers.delete(controlled)) {
    logRecord(game, `${player(game, controlled).name} is no longer controlled`, {
      kind: "control-player",
      player: controlled,
    });
  }
}

/** The list backing a zone. Shared zones ignore `owner`. */
export function zoneList(game: Game, zone: Zone, owner: PlayerId = NO_PLAYER): ObjectId[] {
  switch (zone) {
    case Zone.BATTLEFIELD:
      return game.battlefield;
    case Zone.STACK:
      return game.stack;
    case Zone.EXILE:
      return game.exile;
    case Zone.COMMAND:
      return game.command;
    case Zone.LIBRARY:
      return player(game, requireOwner(zone, owner)).library;
    case Zone.HAND:
      return player(game, requireOwner(zone, owner)).hand;
    case Zone.GRAVEYARD:
      return player(game, requireOwner(zone, owner)).graveyard;
    default:
      throw new RangeError(`no zone ${zone}`);
  }
}

function requireOwner(zone: Zone, owner: PlayerId): PlayerId {
  if (owner === NO_PLAYER) throw new RangeError(`zone ${zone} is player-owned; a player is required`);
  return owner;
}

export function objectsIn(game: Game, zone: Zone, owner: PlayerId = NO_PLAYER): GameObject[] {
  return zoneList(game, zone, owner).map((id) => game.objects.get(id) as GameObject);
}

/**
 * Every permanent, optionally only one controller's. Phased-out permanents
 * are treated as though they don't exist (CR 702.26a).
 */
export function permanents(game: Game, controller?: PlayerId): GameObject[] {
  const out: GameObject[] = [];
  for (const id of game.battlefield) {
    const obj = game.objects.get(id) as GameObject;
    if (obj.phasedOut) continue;
    if (controller === undefined || obj.controller === controller) out.push(obj);
  }
  return out;
}

/** Put a new object into a zone. */
export function createObject(
  game: Game,
  card: unknown,
  owner: PlayerId,
  zone: Zone,
  options: { kind?: number; faceIndex?: number; toTop?: boolean } = {},
): GameObject {
  const obj = newGameObject(allocateObjectId(game.ids), {
    kind: options.kind ?? ObjectKind.CARD,
    owner,
    controller: owner,
    baseController: owner,
    zone,
    card,
    faceIndex: options.faceIndex ?? 0,
    timestamp: allocateTimestamp(game.ids),
  });
  game.objects.set(obj.id, obj);
  const target = zoneList(game, zone, owner);
  if (options.toTop) target.unshift(obj.id);
  else target.push(obj.id);
  return obj;
}
