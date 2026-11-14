/**
 * Casting spells, activating abilities and playing lands (CR 601, 602, 305).
 * Ported from mtgfish/rules/cr600_spells_and_abilities/cr601_casting.py
 * (play_land, cast_spell, activate_ability, announced_x, _activate). The
 * rest of that file lives beside this one by topic: targeting.ts, modes.ts,
 * total_cost.ts, cost_checks.ts, paying.ts.
 *
 * A cast or activation that can't be completed throws CastError, having
 * been rewound (CR 601.2h); the priority loop treats it as not having happened.
 */

import type { Ability } from "../../ir";
import { Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { invalidateCharacteristics, objectText, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import type { Action } from "../cr100_game_concepts/priority";
import { asksForX, isPayable, requiresTapping } from "../cr100_game_concepts/costs";
import { recordCommanderCast } from "../cr100_game_concepts/player";
import { castModeForFace } from "../cr300_card_types/card_types";
import { Act, prohibited } from "../cr500_turn_structure/restrictions";
import { castFaceIndex, entwinedModes, promiseGift, putCastTriggers } from "../cr700_additional_rules/paid_costs";
import { emit } from "../emit";
import { flag, ir, list, num, str } from "../ir_read";
import { ZERO_COST } from "../mana/cost";
import { commanderIdentity } from "../zones";
import { moveObject } from "../zones";
import { CastError } from "./cast_error";
import { characteristicsOf } from "./layers";
import { chooseModes, modalEffect } from "./modes";
import {
  activationLimitReached,
  activationRefusal,
  canPayActivation,
  manaCheckpoint,
  pay,
  payActivation,
  recordActivation,
  restoreManaCheckpoint,
} from "./paying";
import { newResolution, resolveManaAbility } from "./resolve";
import { pushAbility } from "./stack";
import {
  abilityIsTargeted,
  announceTargets,
  chooseTargets,
  spellEffects,
  targetedNodes,
  targetingEffects,
  validateAbilityTargets,
  validateTargets,
} from "./targeting";
import { chosenAlternativeCost, computeTotalCost, recordAlternativeCost, recordedPayments } from "./total_cost";
import { targetOptional } from "./effect_props";
import { resolveManaTriggers } from "./triggers";

export { CastError } from "./cast_error";
export { activationManaCost, canPayActivation, payActivation } from "./paying";
export { chooseModes, legalModes, modalEffect } from "./modes";

const ACTIVATED: number = ir("AbilityKind.ACTIVATED");
const nameOf = (game: Game, obj: GameObject): string => objectText(obj, characteristicsOf(game, obj).name);

/** Play a land (CR 305.1): a special action, no stack, within the land allowance. */
export function playLand(game: Game, playerId: PlayerId, act: Action): boolean {
  const p = player(game, playerId);
  const obj = game.objects.get(act.source);
  if (obj === undefined) throw new CastError("no such card");
  if (p.landsPlayed >= p.maxLands) throw new CastError("land drop already used this turn");
  // CR 101.2: "players can't play lands" stops this as well as the land drop offered.
  if (prohibited(game, Act.PLAY_LAND, { obj, player: playerId }) !== null) throw new CastError("an effect prohibits playing that land");
  const permanent = moveObject(game, obj, Zone.BATTLEFIELD, { toPlayer: playerId });
  permanent.controller = playerId;
  permanent.faceIndex = act.faceIndex; // CR 712.12: an MDFC played as a land, face chosen
  invalidateAllCharacteristics(game);
  p.landsPlayed += 1;
  logRecord(game, `${p.name} plays ${nameOf(game, permanent)}`, { kind: "land", player: playerId });
  emit(game, event(EventKind.LAND_PLAYED, { objectId: permanent.id, player: playerId }));
  return true;
}

/** Keywords whose alternative cost casts the card face down (CR 702.36a, 702.37a, 702.168a). */
const FACE_DOWN_CAST_KEYWORDS = new Set(["Morph", "Megamorph", "Disguise"]);

function isFaceDownCast(game: Game, spell: GameObject, act: Action): boolean {
  if (act.alternativeCost < 0) return false;
  const alternative = chosenAlternativeCost(game, spell, act);
  return alternative !== null && FACE_DOWN_CAST_KEYWORDS.has(str(alternative.keyword));
}

/** Cast a spell, following CR 601.2. Returns the spell on the stack. */
export function castSpell(game: Game, playerId: PlayerId, act: Action): GameObject {
  const cardObject = game.objects.get(act.source);
  if (cardObject === undefined) throw new CastError("no such card");
  const originZone = cardObject.zone;
  const fromCommandZone = originZone === Zone.COMMAND;
  // CR 601.3: Silence stops a cascade or a cast by another spell too.
  if (prohibited(game, Act.CAST_SPELL, { obj: cardObject, player: playerId }) !== null) {
    throw new CastError("an effect prohibits casting that spell");
  }
  // CR 601.2b: an alternative cost from a granted ability is read before CR 400.7 separates them.
  const offered = chosenAlternativeCost(game, cardObject, act);
  // CR 601.2a: to the stack, where it becomes a spell.
  const spell = moveObject(game, cardObject, Zone.STACK, { toPlayer: playerId });
  spell.controller = playerId;
  spell.castFrom = originZone;
  spell.wasCast = true; // CR 601.2, 707.10
  spell.alternativeCostOffered = offered;
  const chosenAlternative = offered ?? chosenAlternativeCost(game, cardObject, act);
  if (chosenAlternative !== null && str(chosenAlternative.keyword).toLowerCase() === "bestow") {
    spell.bestowed = true; // CR 702.103b
    invalidateAllCharacteristics(game);
  }
  spell.castWithoutPaying = cardObject.castWithoutPaying; // CR 118.5
  const preparedBy = cardObject.prepareCopyOf; // CR 722.3c
  spell.faceIndex = castFaceIndex(spell, offered, act.faceIndex); // CR 702.162a, 712.8c
  spell.castMode = castModeForFace(spell, act.faceIndex); // CR 715.3, 718.3, 720.3
  invalidateCharacteristics(spell);
  invalidateAllCharacteristics(game);
  // CR 702.36a, 708.2: a morph spell is face down from the moment it is on the stack.
  if (isFaceDownCast(game, spell, act)) {
    spell.faceDown = true;
    spell.faceDownBy = str(chosenAlternativeCost(game, spell, act)!.keyword); // CR 702.168a
    invalidateCharacteristics(spell);
    invalidateAllCharacteristics(game);
  }

  // CR 601.2h: an incomplete cast is rewound, mana abilities included.
  const checkpoint = manaCheckpoint(game, playerId);
  try {
    // CR 601.2b: optional additional costs (one entry per payment), gift, modes, X.
    spell.additionalCostsPaid = recordedPayments(characteristicsOf(game, spell), act.additionalCosts);
    promiseGift(game, spell, playerId); // CR 702.174a
    const entwined = entwinedModes(game, spell, modalEffect(spellEffects(game, spell))); // CR 702.42a
    spell.chosenModes = entwined.length > 0
      ? entwined
      : chooseModes(game, spell, spellEffects(game, spell), playerId, { announced: act.modeChoices });
    spell.xValue = act.xValue;
    // CR 601.2c-d: targets, chosen now if the action didn't bring them.
    const chosen = act.targets.length > 0 ? act.targets : chooseTargets(game, spell, targetingEffects(game, spell), playerId);
    validateTargets(game, spell, chosen);
    spell.targets = chosen.map((g) => g.slice());
    announceTargets(game, spell, playerId);
    // CR 601.2f: the total cost, only now.
    const total = computeTotalCost(game, spell, playerId, act, fromCommandZone);
    if (!isPayable(total)) throw new CastError("this spell has no mana cost, so its cost is unpayable"); // CR 118.6
    // CR 601.2g-h: "without paying its mana cost" skips only the mana.
    if (spell.castWithoutPaying) {
      total.base = ZERO_COST;
      total.increase = 0;
      total.reduction = 0;
      total.unpayable = false;
    }
    pay(game, playerId, total, spell);
    if (spell.castWithoutPaying) spell.manaSpent = 0;
    recordAlternativeCost(game, spell, act);
  } catch (e) {
    if (!(e instanceof CastError)) throw e;
    restoreManaCheckpoint(game, playerId, checkpoint);
    logRecord(game, "Cast rewound; the game state is unchanged", { kind: "rewind" });
    moveObject(game, spell, originZone, { toPlayer: cardObject.owner });
    throw e;
  }

  if (spell.isCommander && fromCommandZone) {
    // CR 903.8: only casts from the command zone increase the tax.
    recordCommanderCast(player(game, playerId), commanderIdentity(game, spell.id));
    emit(game, event(EventKind.COMMANDER_CAST, { objectId: spell.id, player: playerId }));
  }
  if (preparedBy !== NO_OBJECT) game.preparedPermanents.delete(preparedBy); // CR 722.3b
  logRecord(game, `${player(game, playerId).name} casts ${nameOf(game, spell)}`, { kind: "cast", player: playerId });
  game.spellsCastThisTurn += 1; // CR 731.2
  // CR 601.2i: only now do cast triggers fire.
  emit(game, event(EventKind.CAST_SPELL, { objectId: spell.id, player: playerId }));
  putCastTriggers(game, spell); // CR 702.56a, 702.78a
  return spell;
}

/** Activate an ability (CR 602.2); a mana ability resolves at once (CR 605.3). */
export function activateAbility(game: Game, playerId: PlayerId, act: Action): GameObject | null {
  const source = game.objects.get(act.source);
  if (source === undefined) throw new CastError("no such permanent");
  const ability = (characteristicsOf(game, source).abilities as readonly Ability[])[act.abilityIndex];
  if (ability === undefined) throw new CastError("no such ability");
  if (flag(ability.unparsed)) throw new CastError("ability could not be understood");
  const refusal = activationRefusal(game, playerId, source, ability);
  if (refusal !== null) throw new CastError(refusal);
  if (activationLimitReached(source, ability, act.abilityIndex)) {
    throw new CastError("that ability has already been activated as often as it may be");
  }
  // CR 107.3a, 601.2b: X is announced first; with no X a stray value is ignored.
  const xValue = asksForX(ability.cost) ? Math.max(0, act.xValue) : 0;
  return withAnnouncedX(game, source.id, ability, xValue, () => {
    if (!canPayActivation(game, source, ability, xValue)) throw new CastError("cannot pay the activation cost");
    return activate(game, playerId, act, source, ability, xValue);
  });
}

/**
 * CR 107.3a, 107.3k: while an ability with X in its activation cost is
 * activated or resolves, X is the announced value, not the source's own.
 */
export function withAnnouncedX<T>(game: Game, sourceId: ObjectId, ability: Ability, xValue: number, body: () => T): T {
  const source = game.objects.get(sourceId);
  if (source === undefined || num(ability.kind) !== ACTIVATED || !asksForX(ability.cost)) return body();
  const was = source.xValue;
  source.xValue = xValue;
  try {
    return body();
  } finally {
    source.xValue = was;
  }
}

function activate(game: Game, playerId: PlayerId, act: Action, source: GameObject, ability: Ability, xValue: number): GameObject | null {
  const effects = list(ability.effects);
  // CR 602.2b, 700.2a: modes first; a modal ability with no legal mode can't be activated.
  const chosenModes = chooseModes(game, source, effects, playerId, { announced: act.modeChoices });
  if (chosenModes.length === 0 && modalEffect(effects) !== null) throw new CastError("no legal mode for this ability");

  let targets: readonly (readonly number[])[] = act.targets;
  if (abilityIsTargeted(ability)) {
    if (targets.length === 0) {
      const nodes = targetedNodes(effects, chosenModes.length > 0 ? chosenModes : null);
      targets = chooseTargets(game, source, nodes, playerId);
      nodes.forEach((node, i) => {
        if ((targets[i]?.length ?? 0) === 0 && !targetOptional(node)) throw new CastError("no legal target for this ability");
      });
    }
    validateAbilityTargets(game, source, ability, targets, chosenModes);
  }
  // A fresh record per activation.
  source.costPaidObjects = [];
  payActivation(game, source, ability, xValue);
  recordActivation(source, ability, act.abilityIndex);

  if (flag(ability.is_mana_ability)) {
    // CR 605.3b: at once; the modes travel on the resolution.
    resolveManaAbility(newResolution(game, source.id, playerId, { chosenModes }), effects, { tapped: requiresTapping(ability.cost) });
    resolveManaTriggers(game); // CR 605.4
    return null;
  }
  emit(game, event(EventKind.ABILITY_ACTIVATED, { objectId: source.id, player: playerId }));
  const stackObject = pushAbility(game, source, ability, { targets, xValue, chosenModes });
  announceTargets(game, stackObject, playerId); // CR 602.2b, 702.21a
  stackObject.costPaidObjects = source.costPaidObjects.slice();
  return stackObject;
}
