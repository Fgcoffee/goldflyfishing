/**
 * Turn structure and turn-based actions (CR 500-514). Ported from
 * mtgfish/rules/cr500_turn_structure/cr500_turn.py.
 *
 * A turn is five phases, some with steps. Turn-based actions happen as a step
 * begins, without the stack (CR 703.2). Untap and cleanup grant no priority
 * (CR 502.4, 514.3) - unless, in cleanup, an SBA or trigger happens, and then
 * another cleanup step follows (CR 514.3a).
 */

import { Phase, Step } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import {
  agentFor,
  invalidateAllCharacteristics,
  logRecord,
  nextPlayer,
  permanents,
  player,
  releasePlayer,
  type Game,
} from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { clearPool } from "../cr100_game_concepts/mana_pool";
import { beginPlayerTurn } from "../cr100_game_concepts/player";
import { discard, draw } from "../cr100_game_concepts/actions";
import { runPriority, settle, type Agent } from "../cr100_game_concepts/priority";
import { hasKeyword } from "../cr200_parts_of_a_card/characteristics";
import { sagaLoreCounters } from "../cr300_card_types/card_types";
import { expireAtCleanup, expireAtEndOfCombat, expireAtStartOfTurn } from "../cr600_spells_and_abilities/durations";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { controlsAnAttraction, rollToVisit } from "../cr700_additional_rules/attractions";
import {
  checkDayNightTransition,
  initiativeUpkeep,
  monarchEndStepDraw,
  radCounterMilling,
} from "../cr700_additional_rules/designations";
import { checkStateBasedActions } from "../cr700_additional_rules/sba";
import { emit } from "../emit";
import { attackers, dealCombatDamage, declareAttackers, declareBlockers, endCombat } from "./combat";
import { Act, permitted, prohibited } from "./restrictions";

export const TURN_SEQUENCE: readonly (readonly [Phase, Step])[] = [
  [Phase.BEGINNING, Step.UNTAP],
  [Phase.BEGINNING, Step.UPKEEP],
  [Phase.BEGINNING, Step.DRAW],
  [Phase.PRECOMBAT_MAIN, Step.MAIN],
  [Phase.COMBAT, Step.BEGINNING_OF_COMBAT],
  [Phase.COMBAT, Step.DECLARE_ATTACKERS],
  [Phase.COMBAT, Step.DECLARE_BLOCKERS],
  [Phase.COMBAT, Step.COMBAT_DAMAGE],
  [Phase.COMBAT, Step.END_OF_COMBAT],
  [Phase.POSTCOMBAT_MAIN, Step.MAIN],
  [Phase.ENDING, Step.END_STEP],
  [Phase.ENDING, Step.CLEANUP],
];

/** Rules that vary by format or house rule. */
export interface TurnOptions {
  readonly firstPlayerSkipsFirstDraw: boolean;
  readonly maxRounds: number;
}

export const DEFAULT_TURN_OPTIONS: TurnOptions = Object.freeze({ firstPlayerSkipsFirstDraw: false, maxRounds: 25 });

/** Play a game to its end, or until the turn cap. Returns the game finished. */
export function runGame(game: Game, options: TurnOptions = DEFAULT_TURN_OPTIONS): Game {
  const limit = options.maxRounds * Math.max(1, game.players.length);
  while (!game.gameOver && game.turn < limit) {
    takeTurn(game, options);
    if (game.restartRequested) {
      logRecord(game, "Game restarted; no player won or lost", { kind: "restart" });
      game.gameOver = true;
      game.winners = [];
      break;
    }
    if (game.gameOver) break;
    game.activePlayer = nextActivePlayer(game);
  }
  if (game.restartRequested) {
    // PORT: CR 727 restarts need game setup (cr103_setup), not ported yet;
    // until then a restarted game simply ends with no winner.
    logRecord(game, "Restart requested but game setup is not ported yet", { kind: "unsupported" });
    return game;
  }
  if (game.runaway) {
    logRecord(
      game,
      `Game stopped as a runaway: ${game.runaway}. This is a bug in a card, not a property of the deck - the ability had no cost the engine could charge.`,
      { kind: "runaway" },
    );
  } else if (!game.gameOver) {
    logRecord(game, "Turn cap reached; game is a stall-out", { kind: "stall" });
  }
  return game;
}

/** Whose turn is next. CR 500.7: the most recent extra turn is taken first. */
function nextActivePlayer(game: Game): PlayerId {
  const extra = game.extraTurns.pop();
  return extra !== undefined ? extra : nextPlayer(game, game.activePlayer);
}

/** CR 505.1a-b: main phases counted within the turn. */
interface TurnProgress {
  mainPhases: number;
}

/** CR 602.5b, 606.3: "only once each turn" is counted per turn. */
export function clearTurnActivations(game: Game): void {
  for (const obj of game.objects.values()) if (obj.activationsThisTurn.size > 0) obj.activationsThisTurn.clear();
}

/** Run one complete turn. */
export function takeTurn(game: Game, options: TurnOptions = DEFAULT_TURN_OPTIONS): void {
  game.turn += 1;
  const active = player(game, game.activePlayer);
  game.turnHistory.clear();
  game.triggeredOnceThisTurn.clear();
  game.exhaustedLoopSteps.clear();
  game.continuingLoopSteps.clear();
  for (const p of game.players) beginPlayerTurn(p);
  clearTurnActivations(game);
  checkDayNightTransition(game, game.activePlayer);
  game.spellsCastLastTurn = game.spellsCastThisTurn;
  game.spellsCastThisTurn = 0;
  expireAtStartOfTurn(game);
  logRecord(game, `--- Turn ${game.turn}: ${active.name} ---`, { kind: "turn", player: active.id });
  emit(game, event(EventKind.TURN_BEGAN, { player: game.activePlayer }));
  const progress: TurnProgress = { mainPhases: 0 };
  for (const [phase, step] of TURN_SEQUENCE) {
    if (game.gameOver) return;
    if (game.endTurnRequested && step !== Step.CLEANUP) continue;
    if (game.skippedSteps.has(step)) continue;
    runStep(game, phase, step, options, progress);
    runExtras(game, options, progress);
  }
  emit(game, event(EventKind.TURN_ENDED, { player: game.activePlayer }));
  releasePlayer(game, game.activePlayer);
  game.endTurnRequested = false;
  game.skippedSteps.clear();
  endOfTurnCleanup(game);
}

/**
 * CR 500.8, 500.9: extra phases and steps, after the one that made them, the
 * most recently created first. Drained in a loop: one can create another.
 */
function runExtras(game: Game, options: TurnOptions, progress: TurnProgress): void {
  let guard = 0;
  while ((game.extraSteps.length > 0 || game.extraPhases.length > 0) && !game.gameOver) {
    guard += 1;
    if (guard > 100) {
      logRecord(game, "Extra phase/step loop capped", { kind: "cap" });
      game.extraSteps = [];
      game.extraPhases = [];
      return;
    }
    const step = game.extraSteps.pop();
    if (step !== undefined) {
      runStep(game, game.phase, step, options, progress);
      continue;
    }
    const phase = game.extraPhases.pop() as Phase;
    for (const [sequencePhase, s] of TURN_SEQUENCE) if (sequencePhase === phase) runStep(game, phase, s, options, progress);
  }
}

const FIRST_STEP_OF_PHASE: ReadonlySet<Step> = new Set([Step.UNTAP, Step.MAIN, Step.BEGINNING_OF_COMBAT, Step.END_STEP]);

function runStep(game: Game, phaseIn: Phase, step: Step, options: TurnOptions, progress: TurnProgress): void {
  if (skippedForWantOfAttackers(game, step)) return;
  const phase = mainPhaseKind(phaseIn, step, progress);
  game.phase = phase;
  game.step = step;
  if (FIRST_STEP_OF_PHASE.has(step)) emit(game, event(EventKind.PHASE_BEGAN, { player: game.activePlayer, amount: phase }));
  emit(game, event(EventKind.STEP_BEGAN, { player: game.activePlayer, amount: step }));
  turnBasedActions(game, step, options);
  if (step === Step.CLEANUP) {
    cleanupStep(game);
    return;
  }
  if (step === Step.UNTAP) return;
  runPriority(game);
  if (step === Step.END_OF_COMBAT) endOfCombat(game); // CR 511.3: as the step ends
  emptyManaPools(game, step);
  emit(game, event(EventKind.STEP_ENDED, { player: game.activePlayer, amount: step }));
}

/** CR 505.1a: only the turn's first main phase is precombat. */
function mainPhaseKind(phase: Phase, step: Step, progress: TurnProgress): Phase {
  if (step !== Step.MAIN) return phase;
  progress.mainPhases += 1;
  return progress.mainPhases === 1 ? Phase.PRECOMBAT_MAIN : Phase.POSTCOMBAT_MAIN;
}

/** CR 508.8: with nobody attacking there are no blockers or damage steps. */
function skippedForWantOfAttackers(game: Game, step: Step): boolean {
  if (step !== Step.DECLARE_BLOCKERS && step !== Step.COMBAT_DAMAGE) return false;
  return attackers(game).size === 0;
}

function emptyManaPools(game: Game, step: Step | null = null): void {
  if (game.relaxations.manaPoolsPersist) return;
  // CR 702.189a: combat mana survives the combat phase's step ends.
  const keepCombatMana = step !== null && game.phase === Phase.COMBAT && step !== Step.END_OF_COMBAT;
  for (const p of game.players) {
    const lost = clearPool(p.manaPool, keepCombatMana);
    if (lost > 0) emit(game, event(EventKind.MANA_EMPTIED, { player: p.id, amount: lost }));
  }
}

/** CR 703.1, 703.3: what a step does as it begins, before priority. */
function turnBasedActions(game: Game, step: Step, options: TurnOptions): void {
  if (step === Step.UNTAP) {
    untapStep(game);
  } else if (step === Step.MAIN && game.phase === Phase.PRECOMBAT_MAIN) {
    radCounterMilling(game, game.activePlayer);
    sagaLoreCounters(game);
    if (controlsAnAttraction(game, game.activePlayer)) rollToVisit(game, game.activePlayer);
  } else if (step === Step.UPKEEP) {
    emit(game, event(EventKind.UPKEEP, { player: game.activePlayer }));
    initiativeUpkeep(game);
  } else if (step === Step.END_STEP) {
    emit(game, event(EventKind.END_STEP, { player: game.activePlayer }));
    monarchEndStepDraw(game);
  } else if (step === Step.DRAW) {
    drawStep(game, options);
  } else if (step === Step.DECLARE_ATTACKERS) {
    declareAttackers(game);
  } else if (step === Step.DECLARE_BLOCKERS) {
    declareBlockers(game);
  } else if (step === Step.COMBAT_DAMAGE) {
    dealCombatDamage(game);
  }
}

/** CR 502: phasing, then untapping. */
function untapStep(game: Game): void {
  const active = game.activePlayer;
  for (const p of game.players) p.speedIncreasedThisTurn = false;
  // CR 701.15b: goad lasts until the goading player's next turn.
  for (const id of game.battlefield) game.objects.get(id)?.goadedBy.delete(active);
  for (const id of [...game.battlefield]) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.controller !== active) continue;
    if (obj.phasedOut) {
      obj.phasedOut = false;
      obj.phasingScheduled = false;
      emit(game, event(EventKind.PHASED_IN, { objectId: obj.id, player: active }));
    } else if (hasKeyword(characteristicsOf(game, obj), "Phasing")) {
      obj.phasedOut = true;
      obj.phasingScheduled = true;
      emit(game, event(EventKind.PHASED_OUT, { objectId: obj.id, player: active }));
    }
  }
  invalidateAllCharacteristics(game);
  for (const obj of permanents(game, active)) {
    if (!obj.tapped) continue;
    const blocked = prohibited(game, Act.UNTAP_DURING_UNTAP_STEP, { obj }) ?? prohibited(game, Act.UNTAP, { obj });
    if (blocked !== null) {
      logRecord(game, `${characteristicsOf(game, obj).name} does not untap (${String(blocked)})`, { kind: "restriction" });
      continue;
    }
    obj.tapped = false;
  }
  emit(game, event(EventKind.UNTAP_STEP, { player: active }));
  // CR 302.6: summoning sickness ends; CR 702.30a reads what arrived.
  for (const obj of permanents(game, active)) {
    obj.arrivedSinceLastTurn = obj.newlyControlled;
    obj.newlyControlled = false;
    obj.summoningSick = false;
  }
  invalidateAllCharacteristics(game);
}

/** CR 504.1: the active player draws (CR 103.8a in some formats skips it). */
function drawStep(game: Game, options: TurnOptions): void {
  if (options.firstPlayerSkipsFirstDraw && game.turn === 1 && game.activePlayer === game.turnOrder[0]) {
    logRecord(game, "First player skips their first draw step");
    return;
  }
  draw(game, game.activePlayer, 1);
  emit(game, event(EventKind.DRAW_STEP, { player: game.activePlayer }));
}

function endOfCombat(game: Game): void {
  endCombat(game);
  expireAtEndOfCombat(game);
}

export const MAX_CLEANUP_ROUNDS = 20;

/**
 * CR 514: discard to hand size and clear damage, without priority - unless
 * that caused an SBA or trigger, and then priority and another cleanup.
 */
function cleanupStep(game: Game): void {
  for (let i = 0; i < MAX_CLEANUP_ROUNDS; i += 1) {
    discardToHandSize(game);
    clearDamageAndExpireEffects(game);
    emit(game, event(EventKind.CLEANUP, { player: game.activePlayer }));
    let needsPriority = game.pendingTriggers.length > 0;
    if (checkStateBasedActions(game)) needsPriority = true;
    if (!needsPriority) {
      emptyManaPools(game);
      return;
    }
    settle(game);
    runPriority(game);
    emptyManaPools(game);
    if (game.gameOver) return;
  }
}

/** CR 514.1: the active player discards down to their maximum hand size. */
function discardToHandSize(game: Game): void {
  const p = player(game, game.activePlayer);
  if (permitted(game, Act.DISCARD_TO_HAND_SIZE, { player: p.id }) !== null) return;
  const agent = agentFor(game, p.id) as Agent | undefined;
  while (p.hand.length > p.maxHandSize) {
    const choice = agent !== undefined ? agent.chooseDiscard(game, p.id) : (p.hand[p.hand.length - 1] as number);
    const obj = game.objects.get(choice) ?? (game.objects.get(p.hand[p.hand.length - 1] as number) as GameObject);
    discard(game, obj);
  }
}

/** CR 514.2: damage wears off and until-end-of-turn effects end, together. */
function clearDamageAndExpireEffects(game: Game): void {
  for (const obj of permanents(game)) {
    obj.damage = 0;
    obj.dealtDeathtouchDamage = false;
    obj.regenerationShields = 0;
  }
  expireAtCleanup(game);
  invalidateAllCharacteristics(game);
}

/** Per-turn state outside the cleanup step itself. */
function endOfTurnCleanup(game: Game): void {
  for (const p of game.players) p.maxLands = 1;
  for (const obj of permanents(game)) {
    obj.attackedThisTurn = false;
    obj.blockedThisTurn = false;
  }
}
