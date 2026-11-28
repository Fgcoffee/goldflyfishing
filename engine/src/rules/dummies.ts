/**
 * Dummy abilities the engine now carries out. The parser emits
 * `EffectKind.DUMMY` with a `rule` naming a shape it recognises but no
 * engine could do (mtgfish/parser/dummies.py); the shapes listed in
 * `IMPLEMENTED_DUMMIES` are done here, read off the dummy's own fields
 * (`targets` when the parser gives one, else its words, matched exactly).
 * Not a Python port: Python had no such abilities.
 */

import type { Ability, Effect, ObjectFilter } from "../ir";
import { CardType } from "../kernel/enums";
import { logRecord, permanents, type Game } from "../kernel/game";
import type { GameObject } from "../kernel/gameobject";
import { matchHooks } from "../kernel/hooks";
import type { PlayerId } from "../kernel/ids";
import { Act, prohibited } from "./cr500_turn_structure/restrictions";
import { characteristicsOf } from "./cr600_spells_and_abilities/layers";
import { flag, ir, list, num } from "./ir_read";

/** The dummy shapes the engine carries out; the parser can report their cards as read. */
export const IMPLEMENTED_DUMMIES: ReadonlySet<string> = new Set(["untap-during-others-untap-step", "activation-cost-change"]);

const DUMMY = ir("EffectKind.DUMMY") as number;
const STATIC = ir("AbilityKind.STATIC") as number;

/** Each static ability's dummy effects of one shape, with the object that has them. */
function staticDummies(game: Game, rule: string, controller?: PlayerId): [GameObject, Effect][] {
  const out: [GameObject, Effect][] = [];
  for (const obj of permanents(game, controller)) {
    for (const ability of characteristicsOf(game, obj).abilities as readonly Ability[]) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      for (const effect of list(ability.effects)) if (num(effect.kind) === DUMMY && effect.rule === rule) out.push([obj, effect]);
    }
  }
  return out;
}

const UNTAP_WORDS: Record<string, number> = {
  permanents: 0,
  artifacts: CardType.ARTIFACT,
  creatures: CardType.CREATURE,
  lands: CardType.LAND,
};

/**
 * What "untap ... during each other player's untap step" untaps: the
 * dummy's `targets` if the parser gave one, else its words ("untap all
 * artifacts you control ...", "untap this creature ..."). Null when the
 * words say more than that (Unwinding Clock's kind is fine; "all nonland
 * permanents" isn't read here).
 */
function untapFilter(effect: Effect): ObjectFilter | null {
  if (effect.targets != null) return effect.targets;
  const text = String(effect.text ?? "").toLowerCase();
  if (/^untap this (artifact|creature|permanent) during each other player's untap step\.?$/.test(text)) {
    return { $: "ObjectFilter", source_only: true };
  }
  const m = /^(?:[^.:"]+: )?untap (?:all|each) (permanents|artifacts|creatures|lands) you control during each other player's untap step\.?$/.exec(text);
  if (m === null) return null;
  const types = UNTAP_WORDS[m[1]!]!;
  return { $: "ObjectFilter", controller: ir("ControllerRelation.YOU"), ...(types ? { types_all: types } : {}) } as ObjectFilter;
}

/**
 * CR 502.3 with Seedborn Muse and friends: in each other player's untap
 * step, the controller of such an ability untaps what it names, at the same
 * time as the active player's permanents (and under the same "doesn't
 * untap" restrictions).
 */
export function untapDuringOthersUntapStep(game: Game, active: PlayerId): void {
  for (const p of game.players) {
    if (p.id === active || p.hasLost) continue;
    for (const [source, effect] of staticDummies(game, "untap-during-others-untap-step", p.id)) {
      const filter = untapFilter(effect);
      if (filter === null) continue;
      for (const obj of matchHooks().find(game, filter, { source: source.id, controller: p.id })) {
        if (!obj.tapped || obj.controller !== p.id) continue;
        const blocked = prohibited(game, Act.UNTAP_DURING_UNTAP_STEP, { obj }) ?? prohibited(game, Act.UNTAP, { obj });
        if (blocked !== null) continue;
        obj.tapped = false;
        logRecord(game, `${characteristicsOf(game, obj).name} untaps (${characteristicsOf(game, source).name})`, { kind: "untap", player: p.id });
      }
    }
  }
}

/**
 * CR 601.2f, 602.2b: "This ability costs {1} less to activate for each
 * legendary creature you control" (the channel lands): the generic mana an
 * activation's own dummy rider takes off (negative) or adds (positive),
 * evaluated for the source and its controller.
 */
export function activationCostChange(game: Game, source: GameObject, ability: Ability): number {
  let total = 0;
  for (const effect of list(ability.effects)) {
    if (num(effect.kind) !== DUMMY || effect.rule !== "activation-cost-change" || effect.amount == null) continue;
    total += matchHooks().evaluate(game, effect.amount, { source: source.id, controller: source.controller });
  }
  return total;
}
