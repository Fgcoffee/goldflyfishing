/**
 * Dummy abilities the engine now carries out. The parser emits
 * `EffectKind.DUMMY` with a `rule` naming a shape it recognises but no
 * engine could do (mtgfish/parser/dummies.py); the shapes listed in
 * `IMPLEMENTED_DUMMIES` are done here, read off the dummy's own fields
 * (`targets` when the parser gives one, else its words, matched exactly).
 * Not a Python port: Python had no such abilities.
 */

import type { Ability, Effect, ObjectFilter } from "../ir";
import { CardType, Duration, Zone } from "../kernel/enums";
import { invalidateAllCharacteristics, invalidateObjects, logRecord, permanents, player, type Game, type SpellRider } from "../kernel/game";
import { addCounters } from "../kernel/gameobject";
import { exile } from "./cr100_game_concepts/actions";
import { moveObject } from "./zones";
import { printedCharacteristics } from "./cr600_spells_and_abilities/layers";
import type { GameObject } from "../kernel/gameobject";
import { matchHooks } from "../kernel/hooks";
import type { PlayerId } from "../kernel/ids";
import { Act, prohibited } from "./cr500_turn_structure/restrictions";
import { characteristicsOf, layerFor } from "./cr600_spells_and_abilities/layers";
import { allocateTimestamp } from "../kernel/ids";
import { hasType } from "./cr200_parts_of_a_card/characteristics";
import { flag, ir, list, num } from "./ir_read";

/** The dummy shapes the engine carries out; the parser can report their cards as read. */
export const IMPLEMENTED_DUMMIES: ReadonlySet<string> = new Set(["untap-during-others-untap-step", "activation-cost-change", "begin-game-on-battlefield", "next-spell-rider"]);

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

/** An agent that may decline a Leyline (CR 103.6 says "may"); without one, yes. */
interface OpeningHandChooser {
  chooseOptional?(game: Game, player: PlayerId, effect: unknown): boolean;
}

/**
 * CR 103.6: after mulligans, starting with the starting player and in turn
 * order, each player may put cards from their opening hand that say so onto
 * the battlefield (the Leylines). They begin the game there, so nothing
 * "enters" for triggers. Gemstone Caverns' version only for a player who
 * isn't starting: with a luck counter, exiling another card from hand (the
 * last one in hand, a fixed choice: no agent decides it yet).
 */
export function beginGameOnBattlefield(game: Game): void {
  const starting = game.activePlayer;
  const order = game.turnOrder.length > 0 ? game.turnOrder : game.players.map((p) => p.id);
  const at = Math.max(0, order.indexOf(starting));
  const seats = [...order.slice(at), ...order.slice(0, at)];
  const before = game.suppressTriggers;
  game.suppressTriggers = true;
  try {
    for (const pid of seats) {
      const p = player(game, pid);
      for (const id of p.hand.slice()) {
        const card = game.objects.get(id);
        if (card === undefined || card.zone === undefined) continue;
        for (const ability of printedCharacteristics(game, card).abilities as readonly Ability[]) {
          const effect = list(ability.effects).find((e) => num(e.kind) === DUMMY && e.rule === "begin-game-on-battlefield");
          if (effect === undefined) continue;
          const luck = String(effect.text ?? "").toLowerCase().includes("luck counter");
          if (luck && pid === starting) break;
          const agent = game.agents.get(pid) as OpeningHandChooser | undefined;
          if (agent?.chooseOptional !== undefined && !agent.chooseOptional(game, pid, effect)) break;
          const permanent = moveObject(game, card, Zone.BATTLEFIELD, { toPlayer: pid });
          permanent.summoningSick = false;
          if (luck) {
            addCounters(permanent, "luck", 1);
            const other = p.hand[p.hand.length - 1];
            const exiled = other === undefined ? undefined : game.objects.get(other);
            if (exiled !== undefined) exile(game, exiled);
          }
          logRecord(game, `${characteristicsOf(game, permanent).name} begins the game on the battlefield (CR 103.6)`, { kind: "setup", player: pid });
          break;
        }
      }
    }
  } finally {
    game.suppressTriggers = before;
  }
}

// -- next-spell-rider ---------------------------------------------------------------------

const RIDER_WORDS =
  /^the next (?:(instant or sorcery|creature|noncreature) )?spell you cast this turn (?:(can't be countered)|has (improvise|convoke|cascade))\.?$/;

/** A next-spell-rider dummy's words, read: which spells and what rider. Null for any other wording. */
export function spellRiderOf(effect: Effect): { spells: string; rider: string } | null {
  const m = RIDER_WORDS.exec(String(effect.text ?? "").toLowerCase().trim());
  if (m === null) return null;
  return { spells: m[1] ?? "", rider: m[2] ?? m[3]! };
}

/**
 * Carries out a dummy effect as it resolves; false when its shape isn't one
 * done at resolution. next-spell-rider (Mistrise Village, Archway of
 * Innovation): the controller's next spell this turn that fits gets the
 * rider, when it is cast (rules/casting.ts takes it).
 */
export function resolveDummy(game: Game, controller: PlayerId, effect: Effect): boolean {
  if (effect.rule !== "next-spell-rider") return false;
  const read = spellRiderOf(effect);
  if (read === null) return false;
  game.spellRiders.push({ player: controller, spells: read.spells, rider: read.rider, turn: game.turn });
  logRecord(game, `${player(game, controller).name}'s next ${read.spells ? read.spells + " " : ""}spell this turn: ${read.rider}`, {
    kind: "effect",
    player: controller,
  });
  return true;
}

function riderFits(game: Game, card: GameObject, spells: string): boolean {
  if (spells === "") return true;
  const c = characteristicsOf(game, card);
  if (spells === "creature") return hasType(c, CardType.CREATURE);
  if (spells === "noncreature") return !hasType(c, CardType.CREATURE);
  return hasType(c, CardType.INSTANT) || hasType(c, CardType.SORCERY);
}

/** The riders this card would get if this player cast it now (this turn's, fitting it). */
export function waitingRiders(game: Game, playerId: PlayerId, card: GameObject): SpellRider[] {
  return game.spellRiders.filter((r) => r.player === playerId && r.turn === game.turn && riderFits(game, card, r.spells));
}

/** A rider as the keyword ability the spell has; cascade is a cast trigger instead (riderCascades). */
export function riderAbility(rider: SpellRider): Ability | null {
  if (rider.rider === "cascade") return null;
  const word = rider.rider === "can't be countered" ? "Can't be countered" : rider.rider.charAt(0).toUpperCase() + rider.rider.slice(1);
  return { $: "Ability", kind: STATIC as never, keyword: word, text: word };
}

/**
 * CR 601.2a: as the spell goes on the stack, it takes every waiting rider
 * that fits ("the next spell": all of them, on the one spell), and has
 * their keywords while it is a spell. Stale riders (an earlier turn's) go
 * too. Returns the riders taken, and how to put things back on a rewind.
 */
export function takeSpellRiders(game: Game, playerId: PlayerId, spell: GameObject): { taken: SpellRider[]; undo: () => void } {
  const before = game.spellRiders;
  const effectsBefore = game.continuousEffects.length;
  const taken = waitingRiders(game, playerId, spell);
  game.spellRiders = before.filter((r) => r.turn === game.turn && !taken.includes(r));
  const abilities = taken.map(riderAbility).filter((a): a is Ability => a !== null);
  if (abilities.length > 0) {
    const grant = {
      $: "Effect",
      kind: ir("EffectKind.GRANT_ABILITY"),
      targets: { $: "ObjectFilter", specific: [spell.id], zones: { $set: [Zone.STACK] } },
      granted_abilities: abilities,
      text: "the next spell you cast this turn",
    } as unknown as Effect;
    game.continuousEffects.push({
      effect: grant,
      source: spell.id,
      controller: playerId,
      timestamp: allocateTimestamp(game.ids),
      layer: layerFor(grant),
      duration: Duration.END_OF_TURN,
      createdTurn: game.turn,
      ability: null,
      fromStaticAbility: false,
      isCda: false,
      expired: false,
    } as never);
    invalidateObjects(game, [spell.id]);
  }
  return {
    taken,
    undo: () => {
      game.spellRiders = before;
      if (game.continuousEffects.length > effectsBefore) {
        game.continuousEffects.splice(effectsBefore);
        invalidateAllCharacteristics(game);
      }
    },
  };
}
