/**
 * Whether a condition holds. Ported from mtgfish/rules/kernel/conditions.py.
 *
 * Conditions the TS kernel has no record for yet fail closed, each marked
 * `// PENDING(rules): ...` with what it needs (engine/status/parser.md).
 */

import type { Condition, ObjectFilter, PlayerFilter } from "../../ir";
import { CardType, Phase, Zone } from "../../kernel/enums";
import { looksBackInTime, type GameEvent } from "../../kernel/events";
import { player as playerOf, turnHistoryCountKey, turnHistoryKey, type Game } from "../../kernel/game";
import { counterCount } from "../../kernel/gameobject";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { hasType } from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { atMaxSpeed } from "../cr100_game_concepts/player";
import { list, num, str } from "../ir_read";
import { ConditionKind, ControllerRelation, PlayerScope } from "./enums";
import { combatOf, compare, find, matches, partnerOf, resolvePlayers } from "./matching";
import { evaluate } from "./values";

export interface HoldsOptions {
  readonly source?: ObjectId;
  readonly controller?: PlayerId;
  /** What the resolution last acted on - "if it's blue". */
  readonly remembered?: readonly ObjectId[];
  /** The event a trigger is about. */
  readonly event?: GameEvent | null;
}

const YOU: PlayerFilter = { $: "PlayerFilter", scope: PlayerScope.YOU } as unknown as PlayerFilter;
const OPPONENT: PlayerFilter = { $: "PlayerFilter", scope: PlayerScope.OPPONENT } as unknown as PlayerFilter;

/** CR 702.105a: attacking a player, and nobody still in the game has more life. */
function attackingTheMostLife(game: Game, source: ObjectId): boolean {
  const combat = combatOf(game);
  if (combat === null || !combat.attacking.has(source)) return false;
  if ((combat.attackingPermanent?.get(source) ?? NO_OBJECT) !== NO_OBJECT) return false;
  const defender = combat.attacking.get(source) as PlayerId;
  const living = game.players.filter((p) => !p.hasLost).map((p) => p.life);
  return living.length > 0 && playerOf(game, defender).life >= Math.max(...living);
}

/** Whether something the resolution last acted on fits the description, as it was (last-known information). */
function rememberedMatches(game: Game, condition: Condition, remembered: readonly ObjectId[], controller: PlayerId): boolean {
  if (remembered.length === 0 || !condition.filter) return false;
  const filter = condition.filter;
  return remembered.some((id) => {
    const obj = game.objects.get(id);
    return obj !== undefined && matches(game, obj, filter, { controller, allowStale: true });
  });
}

/** CR 102.3: "an opponent" is some one opponent. */
function existential(players: PlayerFilter | null | undefined): boolean {
  return !!players && num(players.scope) === PlayerScope.OPPONENT;
}

function isEach(players: PlayerFilter | null | undefined): boolean {
  if (!players) return false;
  const scope = num(players.scope);
  return scope === PlayerScope.EACH_OPPONENT || scope === PlayerScope.EACH_PLAYER;
}

/** How much of something happened this turn, against a constraint; "count" asks how many times. */
function happenedThisTurn(game: Game, condition: Condition, controller: PlayerId): boolean {
  const players = resolvePlayers(game, condition.players ?? YOU, { controller });
  if (players.length === 0) return false;
  const counting = (condition.counter_type ?? "") === "count";
  const constraint = condition.constraint;
  const expected = constraint ? evaluate(game, constraint.value, { controller }) : 0;
  const satisfied = (total: number) => (constraint ? compare(constraint.comparison, total, expected) : total > 0);
  const tally = (p: PlayerId) =>
    list(condition.event_kinds).reduce<number>((total, e) => {
      const key = counting ? turnHistoryCountKey(num(e), p) : turnHistoryKey(num(e), p);
      return total + (game.turnHistory.get(key) ?? 0);
    }, 0);
  // "An opponent lost 2 or more life" is about one opponent, not the sum.
  if (existential(condition.players)) return players.some((p) => satisfied(tally(p)));
  if (isEach(condition.players)) return players.every((p) => satisfied(tally(p)));
  return satisfied(players.reduce((t, p) => t + tally(p), 0));
}

/** The trigger event's object, as it last existed for a look-back event (CR 603.10a, 608.2h). */
function triggerObjectMatches(
  game: Game,
  condition: Condition,
  event: GameEvent | null | undefined,
  source: ObjectId,
  controller: PlayerId,
): boolean {
  if (!event || !condition.filter) return false;
  let obj = undefined;
  if (looksBackInTime(event) && event.data.length > 0) obj = game.objects.get(event.data[0] as ObjectId);
  if (obj === undefined) obj = game.objects.get(event.objectId);
  if (obj === undefined) return false;
  return matches(game, obj, condition.filter, { source, controller, allowStale: true });
}

/** One player's count against a value, player by player: "an opponent controls more lands than you". */
function compareCounts(game: Game, condition: Condition, controller: PlayerId): boolean {
  const { filter, constraint } = condition;
  if (!filter || !constraint) return false;
  const players = resolvePlayers(game, condition.players ?? OPPONENT, { controller });
  const target = evaluate(game, constraint.value, { controller });
  const results: boolean[] = [];
  for (const p of players) {
    if (playerOf(game, p).hasLost) continue;
    const spec: ObjectFilter = { ...filter, controller: ControllerRelation.SPECIFIC, controller_specific: p } as ObjectFilter;
    results.push(compare(constraint.comparison, find(game, spec, { controller }).length, target));
  }
  if (results.length === 0) return false;
  return isEach(condition.players) ? results.every(Boolean) : results.some(Boolean);
}

/** Which object a designation condition asks about: a named object, else the source. */
function subjectOf(condition: Condition, source: ObjectId): ObjectId {
  const specific = list(condition.filter?.specific);
  return specific.length > 0 ? (specific[0] as ObjectId) : source;
}

/** Whether a condition is true now. */
export function holds(game: Game, condition: Condition, options: HoldsOptions = {}): boolean {
  const source = options.source ?? NO_OBJECT;
  const controller = options.controller ?? NO_PLAYER;
  const remembered = options.remembered ?? [];
  const event = options.event ?? null;
  const kind = num(condition.kind);
  const constraint = condition.constraint;
  const expectedValue = () => (constraint ? evaluate(game, constraint.value, { source, controller }) : 0);

  switch (kind) {
    case ConditionKind.REMEMBERED_MATCHES:
    case ConditionKind.AFFECTED_MATCHES: // the caller passes the guarded effect's objects as `remembered`
      return rememberedMatches(game, condition, remembered, controller);
    case ConditionKind.TRIGGER_OBJECT_MATCHES:
      return triggerObjectMatches(game, condition, event, source, controller);
    case ConditionKind.VALUE_COMPARE: {
      if (!condition.value || !constraint) return false;
      return compare(constraint.comparison, evaluate(game, condition.value, { source, controller }), expectedValue());
    }
    case ConditionKind.ALWAYS:
      return true;
    case ConditionKind.WAS_CAST:
      return game.objects.get(source)?.wasCast ?? false;
    case ConditionKind.COMPARE_COUNTS:
      return compareCounts(game, condition, controller);
    case ConditionKind.EVENT_THIS_TURN:
      return happenedThisTurn(game, condition, controller);
    case ConditionKind.PLAYER_COUNT: {
      const living = resolvePlayers(game, condition.players ?? YOU, { controller }).filter(
        (p) => !playerOf(game, p).hasLost,
      );
      if (!constraint) return living.length > 0;
      return compare(constraint.comparison, living.length, evaluate(game, constraint.value, { controller }));
    }
    case ConditionKind.AT_MAX_SPEED:
      // CR 702.178a: the controller's speed.
      return controller !== NO_PLAYER && atMaxSpeed(playerOf(game, controller));
    case ConditionKind.NEVER:
    case ConditionKind.UNPARSED:
      return false;
    case ConditionKind.AND:
      return list(condition.operands).every((c) => holds(game, c, options));
    case ConditionKind.OR:
      return list(condition.operands).some((c) => holds(game, c, options));
    case ConditionKind.NOT:
      return !list(condition.operands).some((c) => holds(game, c, options));
    case ConditionKind.OBJECT_COUNT:
    case ConditionKind.CONTROLS_MATCHING: {
      if (!condition.filter || !constraint) return false;
      return compare(constraint.comparison, find(game, condition.filter, { source, controller }).length, expectedValue());
    }
    case ConditionKind.LIFE:
    case ConditionKind.CARDS_IN_HAND: {
      if (!constraint) return false;
      const players = resolvePlayers(game, condition.players ?? YOU, { controller });
      if (players.length === 0) return false;
      const expected = expectedValue();
      const results = players
        .map((id) => playerOf(game, id))
        .filter((p) => !p.hasLost)
        .map((p) => compare(constraint.comparison, kind === ConditionKind.LIFE ? p.life : p.hand.length, expected));
      if (results.length === 0) return false;
      return existential(condition.players) ? results.some(Boolean) : results.every(Boolean);
    }
    case ConditionKind.IS_YOUR_TURN:
      return game.activePlayer === controller;
    case ConditionKind.COUNTER_COUNT: {
      if (!constraint) return false;
      const subject = game.objects.get(subjectOf(condition, source));
      if (subject === undefined) return false;
      return compare(constraint.comparison, counterCount(subject, condition.counter_type ?? ""), expectedValue());
    }
    case ConditionKind.CLASS_LEVEL: {
      // CR 716.2d: no level is level 1.
      if (!constraint) return false;
      const subject = subjectOf(condition, source);
      if (subject === NO_OBJECT) return false;
      return compare(constraint.comparison, game.classLevels.get(subject) ?? 1, expectedValue());
    }
    case ConditionKind.IS_SOLVED: {
      const subject = subjectOf(condition, source);
      return subject !== NO_OBJECT && game.solvedPermanents.has(subject);
    }
    case ConditionKind.NO_MANA_SPENT: {
      const spell = game.objects.get(source);
      return spell !== undefined && !spell.manaSpent;
    }
    case ConditionKind.IS_PAIRED: {
      const obj = game.objects.get(source);
      return obj !== undefined && partnerOf(game, obj) !== null;
    }
    case ConditionKind.IS_SADDLED: {
      const obj = game.objects.get(source);
      return obj !== undefined && obj.zone === Zone.BATTLEFIELD && !!obj.saddledTurn && obj.saddledTurn === game.turn;
    }
    case ConditionKind.ATTACKING_PLAYER_WITH_MOST_LIFE:
      return attackingTheMostLife(game, source);
    case ConditionKind.IS_HARNESSED: {
      const obj = game.objects.get(source);
      return obj !== undefined && obj.harnessed && obj.zone === Zone.BATTLEFIELD;
    }
    case ConditionKind.SPELL_MANA_EXCEEDS_POWER_OR_TOUGHNESS: {
      // CR 702.191a: the trigger event's spell, against this creature.
      if (!event) return false;
      const obj = game.objects.get(source);
      const spell = game.objects.get(event.objectId);
      if (obj === undefined || spell === undefined || obj.zone !== Zone.BATTLEFIELD) return false;
      const chars = characteristicsOf(game, obj);
      if (!hasType(chars, CardType.CREATURE)) return false;
      return spell.manaSpent > (chars.power ?? 0) || spell.manaSpent > (chars.toughness ?? 0);
    }
    case ConditionKind.IS_RENOWNED: {
      const obj = game.objects.get(source);
      return obj !== undefined && obj.renowned && obj.zone === Zone.BATTLEFIELD;
    }
    case ConditionKind.HAS_ENDURING_STORY:
      return controller !== NO_PLAYER && playerOf(game, controller).hasEnduringStory;
    case ConditionKind.COMPLETED_DUNGEON: {
      // CR 309.7: read the record.
      if (controller === NO_PLAYER) return false;
      const completed = playerOf(game, controller).completedDungeons;
      const wanted = str(condition.keyword).toLowerCase();
      if (!wanted) return completed.length > 0;
      return completed.some((name) => name.toLowerCase() === wanted);
    }
    case ConditionKind.HAS_INITIATIVE:
      return controller !== NO_PLAYER && playerOf(game, controller).hasInitiative;
    case ConditionKind.RING_TEMPTED_TIMES: {
      if (!constraint || controller === NO_PLAYER) return false;
      return compare(constraint.comparison, playerOf(game, controller).ringTemptedCount, expectedValue());
    }
    case ConditionKind.FIRST_RESOLUTION_OF_NAME:
      // PENDING(rules): Python reads game.spell_resolutions[(controller, name)]; the TS Game has no such record yet.
      return false;
    case ConditionKind.ALTERNATIVE_COST_PAID: {
      const obj = game.objects.get(source);
      if (obj === undefined || !obj.alternativeCostPaid) return false;
      const wanted = str(condition.keyword);
      return !wanted || obj.alternativeCostPaid.toLowerCase() === wanted.toLowerCase();
    }
    case ConditionKind.WAS_KICKED: {
      // CR 702.33b: the optional additional cost was chosen at announcement.
      const spell = game.objects.get(source);
      if (spell === undefined || spell.additionalCostsPaid.length === 0) return false;
      if (!str(condition.keyword)) return true;
      // PENDING(rules): a named rider ("if its offspring cost was paid") needs
      // Python's paid_additional_keywords (cr702_paid_costs); fails closed.
      return false;
    }
    case ConditionKind.COMBAT_DAMAGE_DEALT_BY:
    case ConditionKind.COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE:
      // PENDING(rules): needs the combat-damage-by record (cr702_cost_keyword_rules); fails closed.
      return false;
    case ConditionKind.IS_STEP: {
      // Both the step and, when given, whose turn it is.
      if (!constraint) return false;
      if (!compare(constraint.comparison, game.step, expectedValue())) return false;
      if (condition.players) {
        return resolvePlayers(game, condition.players, { controller }).includes(game.activePlayer);
      }
      return true;
    }
    case ConditionKind.IS_MAIN_PHASE:
      return game.phase === Phase.PRECOMBAT_MAIN || game.phase === Phase.POSTCOMBAT_MAIN;
    default:
      return false;
  }
}
