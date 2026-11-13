/**
 * Keyword rules about which optional costs were paid for a spell. Ported a
 * piece at a time from cr702_paid_costs.py (paid_additional_keywords,
 * times_paid, entwined_modes, put_cast_triggers) and
 * cr702_cost_keyword_rules.py (promise_gift, cast_face_index).
 */

import type { AdditionalCost, AlternativeCost, Effect } from "../../ir";
import type { CardDef } from "../../kernel/card";
import { agentFor, livingPlayers, type Game } from "../../kernel/game";
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

/**
 * CR 702.56a, 702.78a: replicate's and conspire's "when you cast this spell"
 * copy triggers. PORT: they need PendingTrigger from the triggers port;
 * until then no copy trigger is put on the stack.
 */
export function putCastTriggers(_game: Game, _spell: GameObject): void {}

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
