/**
 * CR 701.54: the Ring tempts you. Ported from
 * mtgfish/rules/cr700_additional_rules/cr701_ring.py.
 *
 * Each time the Ring tempts a player they get the emblem named The Ring if
 * they have none (CR 701.54c), choose a creature they control as their
 * Ring-bearer (701.54a), and their temptation count - which decides how
 * many of the Ring's abilities it has - goes up by one. The abilities are
 * defined by the rule, so they're built here from ordinary IR pieces.
 */

import type { Ability, Condition, Effect, ObjectFilter, TriggerCondition } from "../../ir";
import { Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, invalidateAllCharacteristics, logRecord, permanents, player, type Game } from "../../kernel/game";
import { isLive, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { ir } from "../ir_read";
import { createEmblem } from "../cr100_game_concepts/tokens";
import { isCreature } from "../cr200_parts_of_a_card/characteristics";
import type { Combat } from "../cr500_turn_structure/combat";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { delayedTrigger, type DelayedTrigger } from "../cr600_spells_and_abilities/triggers";

const COMMAND = { $set: [Zone.COMMAND] };
const STATIC = ir("AbilityKind.STATIC");
const TRIGGERED = ir("AbilityKind.TRIGGERED");
const EK = (name: string): number => ir(`EffectKind.${name}` as never) as number;
const YOU = { $: "PlayerFilter" } as const;
const EACH_OPPONENT = { $: "PlayerFilter", scope: ir("PlayerScope.EACH_OPPONENT") } as const;
const constant = (n: number) => ({ $: "Value", constant: n }) as const;

/** CR 701.54e: "your Ring-bearer", read against the asking ability's controller (the emblem's owner). */
export const YOUR_RING_BEARER: ObjectFilter = { $: "ObjectFilter", ring_bearer: true, zones: { $set: [Zone.BATTLEFIELD] } };

/** CR 701.54c: "as long as the Ring has tempted you N or more times" - the count never falls. */
function temptedAtLeast(times: number): Condition {
  return {
    $: "Condition",
    kind: ir("ConditionKind.RING_TEMPTED_TIMES"),
    constraint: { $: "NumericConstraint", comparison: ir("Comparison.GE"), value: constant(times) },
    text: `the Ring has tempted you ${times} or more times`,
  } as Condition;
}

const trigger = (fields: Partial<TriggerCondition>): TriggerCondition => ({ $: "TriggerCondition", functions_in: COMMAND, ...fields }) as TriggerCondition;
const effect = (fields: Partial<Effect>): Effect => ({ $: "Effect", ...fields }) as Effect;

/** The abilities of the emblem named The Ring (CR 701.54c). */
export function ringAbilities(): Ability[] {
  return [
    {
      $: "Ability",
      kind: STATIC,
      effects: [effect({ kind: EK("ADD_TYPE"), targets: YOUR_RING_BEARER, supertypes: ir("Supertype.LEGENDARY"), text: "your Ring-bearer is legendary" })],
      functions_in: COMMAND,
      text: "Your Ring-bearer is legendary and can't be blocked by creatures with greater power.",
    },
    {
      $: "Ability",
      kind: TRIGGERED,
      effects: [
        effect({ kind: EK("DRAW"), players: YOU, amount: constant(1), text: "draw a card" }),
        effect({ kind: EK("DISCARD"), players: YOU, amount: constant(1), text: "discard a card" }),
      ],
      trigger: trigger({ event_kinds: { $set: [EventKind.ATTACKS] }, subject: YOUR_RING_BEARER, intervening_if: temptedAtLeast(2), text: "whenever your Ring-bearer attacks" }),
      functions_in: COMMAND,
      text: "Whenever your Ring-bearer attacks, draw a card, then discard a card.",
    },
    {
      $: "Ability",
      kind: TRIGGERED,
      effects: [effect({ kind: EK("SACRIFICE_BLOCKERS_AT_END_OF_COMBAT"), text: "the blocking creature's controller sacrifices it at end of combat" })],
      trigger: trigger({
        event_kinds: { $set: [EventKind.BECOMES_BLOCKED] },
        subject: YOUR_RING_BEARER,
        intervening_if: temptedAtLeast(3),
        text: "whenever your Ring-bearer becomes blocked by a creature",
      }),
      functions_in: COMMAND,
      text: "Whenever your Ring-bearer becomes blocked by a creature, the blocking creature's controller sacrifices it at end of combat.",
    },
    {
      $: "Ability",
      kind: TRIGGERED,
      effects: [effect({ kind: EK("LOSE_LIFE"), players: EACH_OPPONENT, amount: constant(3), text: "each opponent loses 3 life" })],
      trigger: trigger({
        event_kinds: { $set: [EventKind.COMBAT_DAMAGE_DEALT] },
        source: YOUR_RING_BEARER,
        to_player: true,
        intervening_if: temptedAtLeast(4),
        text: "whenever your Ring-bearer deals combat damage to a player",
      }),
      functions_in: COMMAND,
      text: "Whenever your Ring-bearer deals combat damage to a player, each opponent loses 3 life.",
    },
  ] as Ability[];
}

/**
 * CR 701.54a/e: this player's Ring-bearer, if any. The designation ends
 * when another player gains control and isn't given back, so a stale one is
 * cleared as it is seen.
 */
export function ringBearer(game: Game, playerId: PlayerId): GameObject | null {
  const p = player(game, playerId);
  const obj = game.objects.get(p.ringBearer);
  if (obj === undefined) return null;
  if (!isLive(obj) || obj.zone !== Zone.BATTLEFIELD || obj.controller !== playerId) {
    p.ringBearer = NO_OBJECT;
    return null;
  }
  return obj;
}

export function isRingBearerOf(game: Game, obj: GameObject, playerId: PlayerId): boolean {
  return ringBearer(game, playerId)?.id === obj.id;
}

/** Whether the object is its controller's Ring-bearer. */
export function isARingBearer(game: Game, obj: GameObject): boolean {
  if (obj.controller < 0 || obj.controller >= game.players.length) return false;
  return isRingBearerOf(game, obj, obj.controller);
}

interface RingChooser {
  chooseRingBearer?(game: Game, playerId: PlayerId, creatures: GameObject[]): GameObject | null;
}

/** CR 701.54a: the controller chooses; else greatest power, then lowest id. */
function chooseRingBearer(game: Game, playerId: PlayerId, creatures: GameObject[]): GameObject | null {
  if (creatures.length === 0) return null;
  const agent = agentFor(game, playerId) as RingChooser | null | undefined;
  if (agent && typeof agent.chooseRingBearer === "function") {
    const picked = agent.chooseRingBearer(game, playerId, [...creatures]);
    if (picked !== null && creatures.includes(picked)) return picked;
  }
  const power = (o: GameObject) => characteristicsOf(game, o).power ?? 0;
  return creatures.reduce((best, o) => (power(o) > power(best) || (power(o) === power(best) && o.id < best.id) ? o : best));
}

/** CR 701.54a/c/d: the Ring tempts this player - tempted even with no creature to choose. */
export function tempt(game: Game, playerId: PlayerId): void {
  const p = player(game, playerId);
  const emblem = game.objects.get(p.ringEmblem);
  if (emblem === undefined || !isLive(emblem) || emblem.zone !== Zone.COMMAND) {
    p.ringEmblem = createEmblem(game, playerId, ringAbilities(), { text: "The Ring" }).id;
  }
  const creatures = permanents(game, playerId).filter((o) => isCreature(characteristicsOf(game, o)));
  const chosen = chooseRingBearer(game, playerId, creatures);
  if (chosen !== null) {
    p.ringBearer = chosen.id;
    logRecord(game, `${characteristicsOf(game, chosen).name} becomes ${p.name}'s Ring-bearer`, { kind: "ring", player: playerId });
  }
  p.ringTemptedCount += 1;
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.RING_TEMPTED, { player: playerId, amount: p.ringTemptedCount }));
}

/**
 * The Ring's third ability: each creature blocking the attacker is
 * sacrificed by its controller at end of combat - the beginning of the end
 * of combat step (CR 511.2).
 */
export function sacrificeBlockersAtEndOfCombat(game: Game, attackerId: ObjectId, controller: PlayerId): DelayedTrigger | null {
  const blockers = [...((game.combat as Combat | null)?.blockers.get(attackerId) ?? [])];
  if (blockers.length === 0) return null;
  const delayed = delayedTrigger(
    trigger({ event_kinds: { $set: [EventKind.STEP_BEGAN] }, steps: { $set: [ir("Step.END_OF_COMBAT")] }, functions_in: undefined, text: "at end of combat" }),
    [effect({ kind: EK("SACRIFICE"), targets: { $: "ObjectFilter", specific: blockers }, text: "its controller sacrifices the blocking creature" })],
    controller,
    NO_OBJECT,
  );
  game.delayedTriggers.push(delayed);
  return delayed;
}
