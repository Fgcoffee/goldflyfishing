/**
 * What a player with priority may legally do. Ported from
 * mtgfish/rules/kernel/legality.py. Deterministic order: replay depends on
 * the same list in the same order for the same state.
 */

import type { Ability, AlternativeCost } from "../ir";
import type { CardDef } from "../kernel/card";
import { CardType, Phase, Timing, Zone } from "../kernel/enums";
import { permanents, player, type Game } from "../kernel/game";
import type { GameObject } from "../kernel/gameobject";
import { matchHooks } from "../kernel/hooks";
import { NO_PLAYER, type ObjectId, type PlayerId } from "../kernel/ids";
import { costIsUnparsed, manaComponent, nonManaComponents } from "./cr100_game_concepts/costs";
import { commanderTax } from "./cr100_game_concepts/player";
import { action, ActionKind, PASS, type Action } from "./cr100_game_concepts/priority";
import { Rule, ruleIsSuspended } from "./cr100_game_concepts/rule_overrides";
import { asAction, available as specialAvailable } from "./cr100_game_concepts/special_actions";
import { additionalCosts, alternativeCosts, hasKeyword, hasType, isLand, type Characteristics } from "./cr200_parts_of_a_card/characteristics";
import { Act, permitted, prohibited } from "./cr500_turn_structure/restrictions";
import { CastError } from "./cr600_spells_and_abilities/cast_error";
import { checkPayable } from "./cr600_spells_and_abilities/cost_checks";
import { includesPlayers, targetOptional, targetsAPlayer } from "./cr600_spells_and_abilities/effect_props";
import { characteristicsOf, faceDownCharacteristics, fromFace, intrinsicAbilities, printedCharacteristics } from "./cr600_spells_and_abilities/layers";
import { canProduceFor } from "./cr600_spells_and_abilities/mana_host";
import { abilityTargetsAvailable, legalModes, modalEffect } from "./cr600_spells_and_abilities/modes";
import { activationLimitReached, activationRefusal, canPayActivation, costLeftForSources, helperCapacity } from "./cr600_spells_and_abilities/paying";
import {
  abilityIsTargeted,
  auraTargetEffect,
  candidatesFor,
  distinctTargetsAvailable,
  spellEffects,
  targetBounds,
  targetedNodes,
  targetingEffects,
} from "./cr600_spells_and_abilities/targeting";
import { asItWouldBeCast, cheapestModeCost, chosenCost, costIncreases, costReductions } from "./cr600_spells_and_abilities/total_cost";
import { castableFaceIndices, playableLandFaceIndices } from "./cr700_additional_rules/faces";
import { emergeDiscount } from "./cr700_additional_rules/paid_costs";
import { flag, ir, num, str } from "./ir_read";
import { fromIR, increasedBy, manaValue, plus, reducedBy, type ManaCost } from "./mana/cost";
import { findPayment } from "./mana/payment";
import { paymentContextOf } from "./cr600_spells_and_abilities/mana_host";
import { commanderIdentity } from "./zones";

const ACTIVATED: number = ir("AbilityKind.ACTIVATED");

/** CR 307.1: your main phase, stack empty (and you have priority). */
export function hasSorcerySpeed(game: Game, playerId: PlayerId): boolean {
  return (
    game.activePlayer === playerId &&
    (game.phase === Phase.PRECOMBAT_MAIN || game.phase === Phase.POSTCOMBAT_MAIN) &&
    game.stack.length === 0
  );
}

/** Every action this player could take, passing first. */
export function legalActions(game: Game, playerId: PlayerId): Action[] {
  const sorcerySpeed = hasSorcerySpeed(game, playerId);
  return [
    PASS,
    ...landPlays(game, playerId, sorcerySpeed),
    ...castable(game, playerId, sorcerySpeed),
    ...activatable(game, playerId, sorcerySpeed),
    // CR 116: special actions, in every window a player gets priority.
    ...specialAvailable(game, playerId).map(asAction),
  ];
}

// -- lands (CR 305.1, 116.2a) ------------------------------------------------------

function landPlays(game: Game, playerId: PlayerId, sorcerySpeed: boolean): Action[] {
  const p = player(game, playerId);
  if (!sorcerySpeed) return [];
  // CR 305.2, 505.6b: one land per turn unless a card switched the limit off (CR 101.1).
  if (p.landsPlayed >= p.maxLands && ruleIsSuspended(game, Rule.ONE_LAND_PER_TURN, { player: playerId }) === null) return [];
  if (prohibited(game, Act.PLAY_LAND, { player: playerId }) !== null) return [];
  const out: Action[] = [];
  for (const id of p.hand) {
    const obj = game.objects.get(id)!;
    if (prohibited(game, Act.PLAY_LAND, { obj, player: playerId }) !== null) continue;
    // CR 712.12: an MDFC with a land face is a land drop for that face.
    for (const faceIndex of playableLandFaceIndices(game, obj)) out.push(action(ActionKind.PLAY_LAND, { source: id, faceIndex }));
  }
  return out;
}

// -- casting (CR 601.3) ------------------------------------------------------------

/**
 * Printed characteristics of one face (split halves, MDFC faces).
 * KNOWN: the CR 718.3a prototype face is not built yet (see layers.ts), so a
 * prototyped cast is judged on the printed characteristics.
 */
function faceCharacteristics(game: Game, obj: GameObject, faceIndex: number): Characteristics {
  const card = obj.card as CardDef | null;
  const face = card?.faces[faceIndex];
  if (card == null || face === undefined) return printedCharacteristics(game, obj);
  const abilities = game.abilityProvider.abilitiesFor(card, faceIndex);
  return fromFace(face, [...abilities, ...intrinsicAbilities(face.type_line)]);
}

function castable(game: Game, playerId: PlayerId, sorcerySpeed: boolean): Action[] {
  const out: Action[] = [];
  const p = player(game, playerId);
  const sources: [ObjectId, Zone][] = [
    ...p.hand.map((id): [ObjectId, Zone] => [id, Zone.HAND]),
    // CR 903.8: a commander its owner may cast from the command zone; nothing else there.
    ...game.command
      .filter((id) => game.objects.get(id)!.owner === playerId && game.objects.get(id)!.isCommander)
      .map((id): [ObjectId, Zone] => [id, Zone.COMMAND]),
    ...p.graveyard.map((id): [ObjectId, Zone] => [id, Zone.GRAVEYARD]),
    ...game.exile.filter((id) => game.objects.get(id)!.owner === playerId).map((id): [ObjectId, Zone] => [id, Zone.EXILE]),
  ];
  for (const [id, zone] of sources) {
    const obj = game.objects.get(id);
    if (obj === undefined) continue;
    // CR 601.3, 601.4.
    if (prohibited(game, Act.CAST_SPELL, { obj, player: playerId }) !== null) continue;
    for (const faceIndex of castableFaceIndices(game, obj)) {
      // CR 715.3d: the permission covers one face.
      if (obj.playableFromHereBy !== NO_PLAYER && faceIndex !== obj.playableFace) continue;
      const chars = asItWouldBeCast(game, obj, faceCharacteristics(game, obj, faceIndex)); // CR 601.2a-b
      if (isLand(chars)) continue;
      if (spellTiming(chars) === Timing.SORCERY && !sorcerySpeed) {
        if (permitted(game, Act.CAST_AS_THOUGH_FLASH, { obj, player: playerId }) === null) continue; // CR 113.6
      }
      if (!targetsAvailable(game, obj, playerId)) continue;
      let mayCastFromHere =
        zone === Zone.HAND || zone === Zone.COMMAND || (obj.playableFromHereBy === playerId && game.turn >= obj.playableFromTurn);
      if (hasKeyword(chars, "Aftermath")) mayCastFromHere = zone === Zone.GRAVEYARD; // CR 702.127a
      if (mayCastFromHere && chars.hasManaCost && affordable(game, playerId, obj, zone, chars)) {
        out.push(action(ActionKind.CAST_SPELL, { source: id, faceIndex }));
        // CR 601.2b, 118.8a: announcing an optional additional cost is another way to cast it.
        for (let announced of optionalCostChoices(chars)) {
          while (affordable(game, playerId, obj, zone, chars, announced)) {
            out.push(action(ActionKind.CAST_SPELL, { source: id, faceIndex, additionalCosts: announced }));
            if (!repeatableAlone(chars, announced)) break;
            announced = [...announced, announced[0]!];
          }
        }
      }
    }
    // CR 113.6, 118.9: each alternative cost is a separate option.
    const chars = asItWouldBeCast(game, obj, characteristicsOf(game, obj));
    if (isLand(chars)) continue;
    (alternativeCosts(chars) as AlternativeCost[]).forEach((alternative, index) => {
      const from = alternative.from_zone == null ? null : num(alternative.from_zone);
      if (from !== null && from !== zone) return;
      if (from === null && zone !== Zone.HAND && zone !== Zone.COMMAND) return;
      if (!alternativeAvailableNow(game, playerId, alternative, obj, chars, sorcerySpeed)) return;
      if (!affordableAlternative(game, playerId, alternative, obj, zone)) return;
      if (!targetsAvailableCastAs(game, obj, playerId, alternative)) return;
      out.push(action(ActionKind.CAST_SPELL, { source: id, alternativeCost: index }));
    });
  }
  return out;
}

/** CR 601.2c as this alternative cost makes the spell: bestowed, it needs a creature (CR 702.103b). */
function targetsAvailableCastAs(game: Game, obj: GameObject, playerId: PlayerId, alternative: AlternativeCost): boolean {
  if (!targetsAvailable(game, obj, playerId)) return false;
  if (str(alternative.keyword).toLowerCase() !== "bestow") return true;
  const enchantCreature = {
    $: "Effect" as const, kind: ir("EffectKind.NOTHING"), is_targeted: true,
    targets: { $: "ObjectFilter" as const, types_all: ir("CardType.CREATURE") },
  };
  return candidatesFor(game, obj, enchantCreature, playerId).length > 0;
}

/** Whether this alternative cost may be chosen now: its condition and window (CR 601.2b, 307.1, 702.190a). */
function alternativeAvailableNow(
  game: Game,
  playerId: PlayerId,
  alternative: AlternativeCost,
  obj: GameObject,
  charsIn: Characteristics,
  sorcerySpeed: boolean,
): boolean {
  if (alternative.condition != null && !matchHooks().holds(game, alternative.condition, { source: obj.id, controller: playerId })) return false;
  if (sorcerySpeed || flag(alternative.instant_speed)) return true;
  let chars = charsIn;
  // CR 708.4: cast face down, it is judged as the 2/2 it will be.
  if (["Morph", "Megamorph", "Disguise"].includes(str(alternative.keyword))) chars = faceDownCharacteristics(obj);
  if (spellTiming(chars) !== Timing.SORCERY) return true;
  return permitted(game, Act.CAST_AS_THOUGH_FLASH, { obj, player: playerId }) !== null;
}

/** Whether an alternative cost could plausibly be paid (mana exact, the rest by the dry run). */
function affordableAlternative(game: Game, playerId: PlayerId, alternative: AlternativeCost, obj: GameObject, zone: Zone): boolean {
  if (costIsUnparsed(alternative.cost)) return false;
  for (const component of nonManaComponents(alternative.cost)) {
    try {
      checkPayable(game, playerId, component, obj, { spell: obj });
    } catch (e) {
      if (e instanceof CastError) return false;
      throw e;
    }
  }
  const p = player(game, playerId);
  let cost = manaComponent(alternative.cost);
  if (flag(alternative.pays_mana_cost)) {
    const chars = characteristicsOf(game, obj); // CR 702.133a
    if (!chars.hasManaCost) return false;
    cost = plus(fromIR(chars.manaCost), cost);
  }
  // CR 903.8 with 118.9d: the tax is an increase, charged on an alternative cost too.
  if (zone === Zone.COMMAND && obj.isCommander) cost = increasedBy(cost, commanderTax(p, commanderIdentity(game, obj.id)));
  cost = reducedBy(cost, emergeDiscount(game, playerId, alternative)); // CR 702.119a
  if (findPayment(p.manaPool, cost, { lifeAvailable: p.life }) !== null) return true;
  return canProduceFor(game, playerId, cost, obj);
}

/** CR 307.1: instants and flash can be cast any time (granted timing is asked separately). */
function spellTiming(chars: Characteristics): number {
  if (hasType(chars, CardType.INSTANT) || hasKeyword(chars, "Flash")) return Timing.INSTANT;
  return Timing.SORCERY;
}

/** Whether a player could plausibly pay a bare mana cost (special actions: no cost changes). */
export function canAfford(game: Game, playerId: PlayerId, cost: ManaCost | null): boolean {
  const p = player(game, playerId);
  if (cost === null || manaValue(cost) === 0) return true;
  if (findPayment(p.manaPool, cost, { lifeAvailable: p.life }) !== null) return true;
  return canProduceFor(game, playerId, cost);
}

/** Each optional additional cost alone, then all of them together. */
function optionalCostChoices(chars: Characteristics): number[][] {
  const indices: number[] = [];
  (additionalCosts(chars) as { optional?: boolean; cost: never }[]).forEach((a, i) => {
    if (flag(a.optional) && !costIsUnparsed(a.cost)) indices.push(i);
  });
  const choices = indices.map((i) => [i]);
  if (indices.length > 1) choices.push(indices);
  return choices;
}

const MOST_REPEATS = 10;
const PAID_ANY_NUMBER_OF_TIMES = new Set(["replicate", "multikicker", "squad"]);

function repeatableAlone(chars: Characteristics, announced: readonly number[]): boolean {
  if (announced.length === 0 || new Set(announced).size !== 1 || announced.length >= MOST_REPEATS) return false;
  const additional = (additionalCosts(chars) as { keyword?: string }[])[announced[0]!];
  return PAID_ANY_NUMBER_OF_TIMES.has(str(additional?.keyword).toLowerCase());
}

/** Whether the player could pay for this spell: mana solved exactly by the planner, the rest estimated. */
function affordable(
  game: Game,
  playerId: PlayerId,
  obj: GameObject,
  zone: Zone,
  charsIn: Characteristics | null = null,
  optional: readonly number[] = [],
): boolean {
  const p = player(game, playerId);
  const chars = charsIn ?? characteristicsOf(game, obj);
  let cost = fromIR(chars.manaCost);
  // CR 601.2h: a mandatory additional cost is paid with the rest.
  const additional = additionalCosts(chars) as { optional?: boolean; cost: never }[];
  for (let index = 0; index < additional.length; index += 1) {
    const a = additional[index]!;
    const times = flag(a.optional) ? optional.filter((i) => i === index).length : 1;
    if (!times) continue;
    const charged = chosenCost(game, playerId, a.cost, obj);
    for (let i = 0; i < times; i += 1) cost = plus(cost, manaComponent(charged));
    for (const component of nonManaComponents(charged)) {
      try {
        checkPayable(game, playerId, component, obj, { spell: obj });
      } catch (e) {
        if (e instanceof CastError) return false;
        throw e;
      }
    }
  }
  const cheapest = cheapestModeCost(game, obj); // CR 702.172a
  if (cheapest !== null) cost = plus(cost, manaComponent(cheapest));
  let increase = costIncreases(game, obj, playerId).reduce((a, b) => a + b, 0);
  if (zone === Zone.COMMAND && obj.isCommander) increase += commanderTax(p, commanderIdentity(game, obj.id));
  const reduction = costReductions(game, obj, playerId).reduce((a, b) => a + b, 0);
  cost = reducedBy(increasedBy(cost, increase), reduction);
  // The spell is the context "spend this mana only on creature spells" asks about.
  if (findPayment(p.manaPool, cost, { lifeAvailable: p.life, context: paymentContextOf(obj) }) !== null) return true;
  // CR 702.51a and friends: what helpers can stand in for comes off what sources must make.
  if (helperCapacity(game, obj, playerId)) cost = costLeftForSources(game, obj, playerId, cost);
  return canProduceFor(game, playerId, cost, obj);
}

/** CR 601.2c: a spell needing a target needs a legal one; a charm needs one legal mode (CR 700.2a). */
function targetsAvailable(game: Game, obj: GameObject, playerId: PlayerId): boolean {
  const effects = spellEffects(game, obj);
  const modal = modalEffect(effects);
  let nodes;
  if (modal === null) {
    nodes = targetingEffects(game, obj);
  } else {
    if (legalModes(game, obj, modal, playerId).length === 0) return false; // CR 700.2b
    nodes = targetedNodes(effects, []);
    const enchant = auraTargetEffect(characteristicsOf(game, obj));
    if (enchant !== null) nodes.unshift(enchant);
  }
  for (const node of nodes) {
    if (targetsAPlayer(node) || targetOptional(node) || includesPlayers(node.targets)) continue; // CR 115.4
    const [fewest] = targetBounds(game, node, playerId);
    const found = matchHooks().find(game, node.targets!, { source: obj.id, controller: playerId });
    if (found.length < Math.max(1, fewest)) return false;
  }
  if (nodes.some((n) => flag(n.distinct_from_earlier_targets))) {
    return distinctTargetsAvailable(nodes, nodes.map((n) => candidatesFor(game, obj, n, playerId)));
  }
  return true;
}

// -- activated abilities (CR 602.5) ------------------------------------------------

/** Permanents, hand, owned command-zone cards and graveyard, each by id (CR 113.6, 602.2, 108.4a). */
function activationSources(game: Game, playerId: PlayerId): GameObject[] {
  const byId = (a: GameObject, b: GameObject) => a.id - b.id;
  const objs = (ids: readonly ObjectId[]) => ids.map((id) => game.objects.get(id)!);
  const p = player(game, playerId);
  return [
    ...[...permanents(game, playerId)].sort(byId),
    ...objs(p.hand).sort(byId),
    ...objs(game.command).filter((o) => o.owner === playerId).sort(byId),
    ...objs(p.graveyard).sort(byId),
  ];
}

function activatable(game: Game, playerId: PlayerId, sorcerySpeed: boolean): Action[] {
  const out: Action[] = [];
  for (const obj of activationSources(game, playerId)) {
    (characteristicsOf(game, obj).abilities as readonly Ability[]).forEach((ability, index) => {
      if (num(ability.kind) !== ACTIVATED || flag(ability.unparsed)) return;
      if (activationRefusal(game, playerId, obj, ability) !== null) return;
      if (num(ability.timing, Timing.INSTANT) === Timing.SORCERY && !sorcerySpeed) return;
      // CR 209.2, 606.3: loyalty abilities are sorcery-speed.
      if (flag(ability.is_loyalty_ability) && !sorcerySpeed) return;
      if (activationLimitReached(obj, ability, index)) return;
      if (!canPayActivation(game, obj, ability)) return;
      if (abilityIsTargeted(ability) && !abilityTargetsAvailable(game, obj, ability, playerId)) return;
      const act = flag(ability.is_loyalty_ability)
        ? Act.ACTIVATE_LOYALTY_ABILITY
        : flag(ability.is_mana_ability)
          ? Act.ACTIVATE_MANA_ABILITY // CR 605: split second's carve-out
          : Act.ACTIVATE_ABILITY;
      if (prohibited(game, act, { obj, player: playerId }) !== null) return;
      const kind = flag(ability.is_mana_ability) ? ActionKind.ACTIVATE_MANA_ABILITY : ActionKind.ACTIVATE_ABILITY;
      out.push(action(kind, { source: obj.id, abilityIndex: index }));
    });
  }
  return out;
}

