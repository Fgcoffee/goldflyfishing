/**
 * Keyword rules about which optional costs were paid for a spell. Ported a
 * piece at a time from cr702_paid_costs.py (paid_additional_keywords,
 * times_paid, entwined_modes, put_cast_triggers) and
 * cr702_cost_keyword_rules.py (promise_gift, cast_face_index).
 */

import type { Ability, AdditionalCost, AlternativeCost, Effect, ObjectFilter } from "../../ir";
import { event, EventKind } from "../../kernel/events";
import { pendingTrigger } from "../cr600_spells_and_abilities/triggers";
import { MAY_CHOOSE_NEW_TARGETS } from "./faces";
import { Zone } from "../../kernel/enums";
import { execute, newResolution } from "../cr600_spells_and_abilities/resolve";
import type { CardDef } from "../../kernel/card";
import { agentFor, invalidateAllCharacteristics, livingPlayers, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { additionalCosts, charsManaValue } from "../cr200_parts_of_a_card/characteristics";
import { matchHooks } from "../../kernel/hooks";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { ir, list, num, str } from "../ir_read";

const SACRIFICE: number = ir("CostKind.SACRIFICE");

/** The keywords of the optional additional costs paid, one per payment, lower-cased. */
export function paidAdditionalKeywords(game: Game, obj: GameObject): string[] {
  const costs = additionalCosts(characteristicsOf(game, obj)) as AdditionalCost[];
  return obj.additionalCostsPaid.filter((i) => i >= 0 && i < costs.length).map((i) => str(costs[i]!.keyword).toLowerCase());
}

export const timesPaid = (game: Game, obj: GameObject, keyword: string): number =>
  paidAdditionalKeywords(game, obj).filter((k) => k === keyword.toLowerCase()).length;

/** CR 702.42a-b: every mode, in printed order, when entwine was paid. */
export function entwinedModes(game: Game, spell: GameObject, modal: Effect | null): number[] {
  if (modal === null || !paidAdditionalKeywords(game, spell).includes("entwine")) return [];
  return list(modal.children).map((_, i) => i);
}

/** CR 702.174a: promising a gift chooses an opponent as the spell is cast. */
export function promiseGift(game: Game, spell: GameObject, playerId: PlayerId): void {
  if (!paidAdditionalKeywords(game, spell).includes("gift")) return;
  const opponents = livingPlayers(game).map((p) => p.id).filter((p) => p !== playerId);
  if (opponents.length === 0) return;
  const agent = agentFor(game, playerId) as { chooseGiftRecipient?(g: Game, p: PlayerId, o: PlayerId[]): PlayerId } | null;
  const chosen = agent?.chooseGiftRecipient?.(game, playerId, opponents);
  spell.chosenPlayer = chosen !== undefined && opponents.includes(chosen) ? chosen : opponents[0]!;
}

/** CR 702.162a: alternative costs that cast the card converted. */
const CONVERTED_CAST_KEYWORDS = new Set(["more than meets the eye"]);

/** The face a spell is cast with: its back face when cast converted (CR 712.8c). */
export function castFaceIndex(spell: GameObject, alternative: AlternativeCost | null, faceIndex: number): number {
  if (alternative === null || !CONVERTED_CAST_KEYWORDS.has(str(alternative.keyword).toLowerCase())) return faceIndex;
  if (((spell.card as CardDef | null)?.faces.length ?? 0) < 2) return faceIndex;
  return 1;
}

const COPIED_AS_CAST: readonly [string, string][] = [
  ["replicate", "copy it for each time its replicate cost was paid"],
  ["conspire", "copy it"],
];

/**
 * CR 702.56a, 702.78a: the "when you cast this spell" copy triggers.
 * Replicate copies once per payment, each paid conspire once (CR 702.78b);
 * they go on the stack above the spell.
 */
export function putCastTriggers(game: Game, spell: GameObject): void {
  for (const [keyword, text] of COPIED_AS_CAST) putCopyTrigger(game, spell, keyword, text);
}

function putCopyTrigger(game: Game, spell: GameObject, keyword: string, text: string): void {
  const count = timesPaid(game, spell, keyword);
  if (!count) return;
  const ability: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    trigger: {
      $: "TriggerCondition",
      event_kinds: { $set: [EventKind.CAST_SPELL] },
      functions_in: { $set: [Zone.STACK] },
      text: `when you cast this spell, if a ${keyword} cost was paid for it`,
    },
    effects: [
      {
        $: "Effect",
        kind: EK("COPY_SPELL"),
        targets: { $: "ObjectFilter", specific: [spell.id], zones: { $set: [Zone.STACK] } },
        amount: { $: "Value", constant: count },
        // CR 702.56a, 702.78a: the copies may be given new targets.
        keywords: [MAY_CHOOSE_NEW_TARGETS],
        text,
      },
    ],
    text: keyword.charAt(0).toUpperCase() + keyword.slice(1),
  };
  game.pendingTriggers.push(
    pendingTrigger(spell.id, ability, event(EventKind.CAST_SPELL, { objectId: spell.id, player: spell.controller }), spell.controller),
  );
}

/** The most an emerge cost could be reduced by now (CR 702.119a), for legality's upper bound. */
export function emergeDiscount(game: Game, playerId: PlayerId, alternative: AlternativeCost): number {
  if (str(alternative.keyword).toLowerCase() !== "emerge") return 0;
  let best = 0;
  for (const component of list(alternative.cost.components)) {
    if (num(component.kind) !== SACRIFICE || component.filter == null) continue;
    for (const obj of matchHooks().find(game, component.filter, { controller: playerId })) {
      best = Math.max(best, charsManaValue(characteristicsOf(game, obj)));
    }
  }
  return best;
}

// -- what a paid alternative cost does to the permanent (CR 702.109a, 702.152a, 702.185a)

/** CR 603.7c: "it" in a delayed ability is the permanent the spell became. */
const IT: ObjectFilter = { $: "ObjectFilter", remembered: true };
const SELF: ObjectFilter = { $: "ObjectFilter", source_only: true };
/** The marker an exile carries for CR 702.185a's permission (cast it after this turn). */
export const WARPED = "warped";
const EK = (name: string): never => ir(`EffectKind.${name}`);

function atTheNextEndStep(text: string, ...children: Effect[]): Effect {
  return {
    $: "Effect", kind: EK("DELAYED_TRIGGER"), children, text,
    trigger: { $: "TriggerCondition", event_kinds: { $set: [ir("EventKind.END_STEP")] }, text: "at the beginning of the next end step" },
  } as Effect;
}

function haste(text: string): Effect {
  // No duration end: it lasts while this object is there (CR 400.7, 611.2c).
  return {
    $: "Effect", kind: EK("GRANT_ABILITY"), targets: SELF, text, duration: ir("Duration.PERMANENT"),
    granted_abilities: [{ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Haste", text: "Haste" }],
  };
}

function whenItDiesDrawACard(): Effect {
  return {
    $: "Effect", kind: EK("GRANT_ABILITY"), targets: SELF, duration: ir("Duration.PERMANENT"),
    text: 'as long as its blitz cost was paid, it has "when this dies, draw a card"',
    granted_abilities: [{
      $: "Ability", kind: ir("AbilityKind.TRIGGERED"), text: "when this dies, draw a card",
      trigger: {
        $: "TriggerCondition", event_kinds: { $set: [ir("EventKind.DIES")] },
        subject: { $: "ObjectFilter", source_only: true, zones: { $set: [] } },
        uses_last_known_information: true,
        text: "when this permanent is put into a graveyard from the battlefield",
      },
      effects: [{ $: "Effect", kind: EK("DRAW"), players: { $: "PlayerFilter", scope: ir("PlayerScope.YOU") }, amount: { $: "Value", constant: 1 }, text: "draw a card" }],
    }],
  } as Effect;
}

function riders(keyword: string): Effect[] {
  if (keyword === "dash") {
    return [
      haste("as long as its dash cost was paid, it has haste"),
      atTheNextEndStep("return it to its owner's hand at the beginning of the next end step",
        { $: "Effect", kind: EK("RETURN_TO_HAND"), targets: IT, text: "return it to hand" }),
    ];
  }
  if (keyword === "blitz") {
    return [
      haste("as long as its blitz cost was paid, it has haste"),
      whenItDiesDrawACard(),
      atTheNextEndStep("sacrifice it at the beginning of the next end step",
        { $: "Effect", kind: EK("SACRIFICE"), targets: IT, text: "sacrifice it" }),
    ];
  }
  if (keyword === "warp") {
    return [
      atTheNextEndStep("exile it at the beginning of the next end step",
        { $: "Effect", kind: EK("EXILE"), targets: IT, keywords: [WARPED], text: "exile it" }),
    ];
  }
  return [];
}

/** The parts of a paid alternative cost that outlast the spell, run as it becomes a permanent (CR 608.3). */
export function onPermanentSpellResolved(game: Game, permanent: GameObject): void {
  const effects = riders(permanent.alternativeCostPaid.toLowerCase());
  if (effects.length === 0) return;
  execute(newResolution(game, permanent.id, permanent.controller, { remembered: [permanent.id] }), effects);
  invalidateAllCharacteristics(game);
}

/** CR 702.27a: a paid buyback cost sends a resolving instant or sorcery to its owner's hand. */
export function resolutionDestination(game: Game, spell: GameObject): Zone | null {
  return paidAdditionalKeywords(game, spell).includes("buyback") ? Zone.HAND : null;
}
