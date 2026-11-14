/**
 * Priority (CR 117) and the agent interface that decides what to do with it.
 * Ported from mtgfish/rules/cr100_game_concepts/cr117_priority.py.
 *
 * 1. A player would receive priority. First state-based actions are performed
 *    until none apply and waiting triggers go on the stack (CR 117.5, 704.3,
 *    603.3b); if either did anything, that repeats.
 * 2. The player acts, or passes. Acting returns priority to them (CR 117.3c).
 * 3. When every player passes in succession, the top of the stack resolves -
 *    or, on an empty stack, the step or phase ends (CR 117.4). Any action by
 *    anyone resets "in succession".
 */

import { NO_PRIORITY_STEPS } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, apnapOrder, logRecord, player, type Game } from "../../kernel/game";
import type { ObjectId, PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { legalActions } from "../legality";
import { actionStep, beginWindow, isExhausted, noteStep, resolutionStep } from "../loops";
import { activateAbility, CastError, castSpell, playLand } from "../cr600_spells_and_abilities/casting";
import { perform as performSpecial } from "./special_actions";
import { resolveTop } from "../cr600_spells_and_abilities/stack";
import { checkStateTriggers, putTriggersOnStack } from "../cr600_spells_and_abilities/triggers";
import { checkStateBasedActions } from "../cr700_additional_rules/sba";

export const ActionKind = {
  PASS: 0,
  CAST_SPELL: 1,
  ACTIVATE_ABILITY: 2,
  PLAY_LAND: 3,
  ACTIVATE_MANA_ABILITY: 4,
  SPECIAL: 5,
} as const;
export type ActionKind = number;

/** Something a player with priority can do. */
export interface Action {
  readonly kind: ActionKind;
  readonly source: ObjectId;
  readonly abilityIndex: number;
  /** One group of chosen targets per targeting instruction. */
  readonly targets: readonly (readonly number[])[];
  readonly xValue: number;
  readonly faceIndex: number;
  readonly modeChoices: readonly number[];
  readonly alternativeCost: number;
  readonly additionalCosts: readonly number[];
}

export function action(kind: ActionKind, fields: Partial<Omit<Action, "kind">> = {}): Action {
  return {
    kind,
    source: 0,
    abilityIndex: -1,
    targets: [],
    xValue: 0,
    faceIndex: 0,
    modeChoices: [],
    alternativeCost: -1,
    additionalCosts: [],
    ...fields,
  };
}

export const PASS: Action = Object.freeze(action(ActionKind.PASS));

/**
 * Whoever decides for a player. The engine only calls these; rarer choices
 * (combat damage assignment, modes, protectors, ...) are optional methods the
 * engine probes for and defaults when absent, as Python did with hasattr.
 */
export interface Agent {
  chooseAction(game: Game, player: PlayerId, legal: readonly Action[]): Action;
  chooseOptional(game: Game, player: PlayerId, effect: unknown): boolean;
  chooseDiscard(game: Game, player: PlayerId): ObjectId;
  orderTriggers(game: Game, player: PlayerId, triggers: unknown[]): unknown[];
  chooseTargets(game: Game, player: PlayerId, source: ObjectId, candidates: readonly (readonly number[])[]): number[][];
  /** Told when a chosen action could not be carried out. */
  actionFailed?(game: Game, player: PlayerId, action: Action): void;
}

/**
 * Passes on everything and takes every optional effect: the engine runs, and
 * a game where nobody acts still exercises turns, SBAs and triggers.
 */
export class PassingAgent implements Agent {
  chooseAction(): Action {
    return PASS;
  }
  chooseOptional(): boolean {
    return true;
  }
  chooseDiscard(game: Game, playerId: PlayerId): ObjectId {
    const hand = player(game, playerId).hand;
    return hand[hand.length - 1] ?? 0;
  }
  orderTriggers(_game: Game, _player: PlayerId, triggers: unknown[]): unknown[] {
    return triggers;
  }
  chooseTargets(_game: Game, _player: PlayerId, _source: ObjectId, candidates: readonly (readonly number[])[]): number[][] {
    // CR 601.2c: a target must be chosen if one exists; the first is
    // arbitrary but deterministic, which replay needs.
    return candidates.map((group) => (group.length > 0 ? [group[0] as number] : []));
  }
}

export const MAX_PRIORITY_ROUNDS = 2000;
export const ACTION_BUDGET = 20_000;

/**
 * Perform SBAs and put triggers on the stack until neither does anything
 * (CR 117.5): each can cause the other.
 */
export function settle(game: Game): void {
  for (let i = 0; i < MAX_PRIORITY_ROUNDS; i += 1) {
    const didSba = checkStateBasedActions(game);
    if (game.gameOver) return;
    checkStateTriggers(game);
    const putAny = putTriggersOnStack(game) > 0;
    if (!didSba && !putAny) return;
  }
}

export function givePriority(game: Game, playerId: PlayerId): void {
  game.priorityPlayer = playerId;
  emit(game, event(EventKind.PRIORITY_RECEIVED, { player: playerId }));
}

/**
 * Run the priority loop until the current step or phase should end: every
 * player has passed in succession on an empty stack.
 */
export function runPriority(game: Game): void {
  if (NO_PRIORITY_STEPS.has(game.step) && game.pendingTriggers.length === 0) {
    settle(game);
    if (game.stack.length === 0 && game.pendingTriggers.length === 0) return;
  }
  let order = apnapOrder(game);
  if (order.length === 0) return;
  let current = order.includes(game.activePlayer) ? game.activePlayer : (order[0] as PlayerId);
  let passedInSuccession = 0;
  beginWindow(game);
  for (let round = 0; round < MAX_PRIORITY_ROUNDS; round += 1) {
    if (game.gameOver) return;
    settle(game);
    if (game.gameOver) return;
    order = apnapOrder(game);
    if (order.length === 0) return;
    if (!order.includes(current)) current = order[0] as PlayerId;
    givePriority(game, current);
    const chosen = decide(game, current);
    const passing = chosen.kind === ActionKind.PASS;
    const step = passing ? null : actionStep(game, current, chosen);
    const acted = !passing && perform(game, current, chosen);
    if (!acted && !passing) {
      (agentFor(game, current) as Agent | undefined)?.actionFailed?.(game, current, chosen);
    }
    if (!acted) {
      passedInSuccession += 1;
      if (passedInSuccession >= order.length) {
        if (game.stack.length > 0) {
          const resolving = resolutionStep(game, game.objects.get(game.stack[game.stack.length - 1] as ObjectId));
          resolveTop(game);
          passedInSuccession = 0;
          current = order.includes(game.activePlayer) ? game.activePlayer : (order[0] as PlayerId);
          if (noteStep(game, resolving) && game.gameOver) return;
          continue;
        }
        return;
      }
      current = nextInOrder(order, current);
      continue;
    }
    passedInSuccession = 0;
    if (step !== null && noteStep(game, step) && game.gameOver) return;
    game.actionsTaken += 1;
    if (game.actionsTaken > ACTION_BUDGET) {
      game.runaway = `${actionKindName(chosen.kind)} ${cardName(game, chosen.source) || "?"}`;
      logRecord(game, `Action budget spent; stopping. Last action: ${game.runaway}`, { kind: "runaway" });
      game.gameOver = true;
      return;
    }
  }
  const topId = game.stack[game.stack.length - 1];
  const name = topId === undefined ? "" : cardName(game, topId) || "?";
  game.runaway = name ? `RESOLVE ${name}` : "PRIORITY LOOP";
  logRecord(game, `Priority loop hit its iteration cap; stopping. Top of stack: ${name || "empty"}`, { kind: "runaway" });
  game.gameOver = true;
}

function cardName(game: Game, id: ObjectId): string {
  const card = game.objects.get(id)?.card as { name?: string } | null | undefined;
  return card?.name ?? "";
}

function actionKindName(kind: ActionKind): string {
  return Object.entries(ActionKind).find(([, v]) => v === kind)?.[0] ?? String(kind);
}

function nextInOrder(order: readonly PlayerId[], current: PlayerId): PlayerId {
  const index = order.indexOf(current);
  if (index < 0) return order[0] as PlayerId;
  return order[(index + 1) % order.length] as PlayerId;
}

function decide(game: Game, playerId: PlayerId): Action {
  const agent = agentFor(game, playerId) as Agent | undefined;
  if (agent === undefined) return PASS;
  let legal = legalActions(game, playerId);
  if (game.exhaustedLoopSteps.size > 0) legal = legal.filter((a) => !isExhausted(game, playerId, a));
  if (legal.length === 0) return PASS;
  return agent.chooseAction(game, playerId, legal) ?? PASS;
}

/**
 * Carry out a chosen action. A CastError means CR 601.2h already rewound the
 * attempt, so the game is exactly as it was: not a crash, just an action that
 * did not happen (legality is an upper bound on what can be paid).
 */
export function perform(game: Game, playerId: PlayerId, chosen: Action): boolean {
  try {
    switch (chosen.kind) {
      case ActionKind.CAST_SPELL:
        castSpell(game, playerId, chosen);
        break;
      case ActionKind.ACTIVATE_ABILITY:
      case ActionKind.ACTIVATE_MANA_ABILITY:
        activateAbility(game, playerId, chosen);
        break;
      case ActionKind.PLAY_LAND:
        playLand(game, playerId, chosen);
        break;
      case ActionKind.SPECIAL:
        return performSpecial(game, playerId, chosen);
      default:
        break;
    }
  } catch (err) {
    if (err instanceof CastError) {
      logRecord(game, `Action abandoned: ${err.message}`, { kind: "illegal", player: playerId });
      return false;
    }
    throw err;
  }
  return true;
}
