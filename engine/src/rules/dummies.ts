/**
 * Dummy abilities the engine now carries out. The parser emits
 * `EffectKind.DUMMY` with a `rule` naming a shape it recognises but no
 * engine could do (mtgfish/parser/dummies.py); the shapes listed in
 * `IMPLEMENTED_DUMMIES` are done here, read off the dummy's own fields
 * (`targets` when the parser gives one, else its words, matched exactly).
 * Not a Python port: Python had no such abilities.
 */

import type { Ability, Effect, ObjectFilter } from "../ir";
import { CardType, Color, Duration, Zone } from "../kernel/enums";
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
import { allocateTimestamp, NO_OBJECT, type ObjectId } from "../kernel/ids";
import { hasType } from "./cr200_parts_of_a_card/characteristics";
import { addMana, manaOfKind, removeMana, type ManaKind, type ManaRider } from "./mana/pool";
import { pendingTrigger } from "./cr600_spells_and_abilities/triggers";
import { MAY_CHOOSE_NEW_TARGETS } from "./cr700_additional_rules/faces";
import { event, EventKind } from "../kernel/events";
import { commanderIdentity } from "./zones";
import { flag, ir, list, num } from "./ir_read";

/** The dummy shapes the engine carries out; the parser can report their cards as read. */
export const IMPLEMENTED_DUMMIES: ReadonlySet<string> = new Set(["untap-during-others-untap-step", "activation-cost-change", "begin-game-on-battlefield", "next-spell-rider", "mana-not-lost", "mana-spent-trigger"]);

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
 * mana-not-lost (Birgi, Savage Ventmaw, CR 500.4): "this mana", what the
 * same resolution just added, is kept as steps and phases end until the
 * turn ends (it goes as the cleanup step ends). Mana already spent from it
 * isn't touched.
 */
function keepManaUntilEndOfTurn(game: Game, manaAdded: readonly [PlayerId, ManaKind, number][]): boolean {
  for (const [pid, kind, amount] of manaAdded) {
    if (kind.untilEndOfTurn) continue;
    const pool = player(game, pid).manaPool;
    const n = Math.min(amount, manaOfKind(pool, kind));
    if (n <= 0) continue;
    removeMana(pool, kind, n);
    addMana(pool, { ...kind, untilEndOfTurn: true }, n);
  }
  return true;
}

/**
 * Carries out a dummy effect as it resolves; false when its shape isn't one
 * done at resolution. next-spell-rider (Mistrise Village, Archway of
 * Innovation): the controller's next spell this turn that fits gets the
 * rider, when it is cast (rules/casting.ts takes it).
 */
export function resolveDummy(
  game: Game,
  controller: PlayerId,
  effect: Effect,
  manaAdded: readonly [PlayerId, ManaKind, number][] = [],
  source: ObjectId = NO_OBJECT,
): boolean {
  if (effect.rule === "mana-not-lost") return keepManaUntilEndOfTurn(game, manaAdded);
  if (effect.rule === "mana-spent-trigger") return tagSpentRider(game, effect, manaAdded, source);
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

/** Abilities a spell has while it is a spell, from a rider (an effect aimed at it alone). */
function grantToSpell(
  game: Game,
  spell: GameObject,
  controller: PlayerId,
  abilities: readonly Ability[],
  text: string,
  duration: number = Duration.END_OF_TURN,
): void {
  if (abilities.length === 0) return;
  const grant = {
    $: "Effect",
    kind: ir("EffectKind.GRANT_ABILITY"),
    targets: { $: "ObjectFilter", specific: [spell.id], zones: { $set: [Zone.STACK] } },
    granted_abilities: abilities,
    text,
  } as unknown as Effect;
  game.continuousEffects.push({
    effect: grant,
    source: spell.id,
    controller,
    timestamp: allocateTimestamp(game.ids),
    layer: layerFor(grant),
    duration,
    createdTurn: game.turn,
    ability: null,
    fromStaticAbility: false,
    isCda: false,
    expired: false,
  } as never);
  invalidateObjects(game, [spell.id]);
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
  grantToSpell(game, spell, playerId, abilities, "the next spell you cast this turn");
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

// -- mana-spent-trigger -------------------------------------------------------------------

const SPENT_WORDS =
  /^(?:when|if) that mana is spent (?:to cast|on) an? (red |dragon |multicolored )?(instant or sorcery|creature) spell( that shares a creature type with your commander)?, (scry (\d+)|that spell can't be countered|copy that spell and you may choose new targets for the copy|it gains haste( until end of turn)?)\.?$/;

/** A mana-spent-trigger dummy's words, read; null for a wording not done yet (riot, a +1/+1 counter). */
export function spentRiderOf(
  text: string,
): { quality: string; spells: string; sharesCommander: boolean; does: string; n: number; untilEndOfTurn: boolean } | null {
  const m = SPENT_WORDS.exec(text.toLowerCase().trim());
  if (m === null) return null;
  const words = m[4]!;
  const does = m[5] !== undefined ? "scry" : words.startsWith("copy") ? "copy" : words.startsWith("it gains haste") ? "haste" : "can't be countered";
  return {
    quality: (m[1] ?? "").trim(),
    spells: m[2]!,
    sharesCommander: m[3] !== undefined,
    does,
    n: Number(m[5] ?? 1),
    untilEndOfTurn: m[6] !== undefined,
  };
}

/** "That mana": what the same resolution's ADD_MANA made carries the rider until spent. */
function tagSpentRider(game: Game, effect: Effect, manaAdded: readonly [PlayerId, ManaKind, number][], source: ObjectId): boolean {
  const text = String(effect.text ?? "");
  if (spentRiderOf(text) === null) return false;
  for (const [pid, kind, amount] of manaAdded) {
    if (kind.spentRider !== null) continue;
    const pool = player(game, pid).manaPool;
    const n = Math.min(amount, manaOfKind(pool, kind));
    if (n <= 0) continue;
    removeMana(pool, kind, n);
    addMana(pool, { ...kind, spentRider: { source, text } }, n);
  }
  return true;
}

function commanderCreatureTypes(game: Game, playerId: PlayerId): Set<string> {
  const out = new Set<string>();
  for (const id of player(game, playerId).commanders) {
    const obj = game.objects.get(commanderIdentity(game, id)) ?? game.objects.get(id);
    if (obj === undefined || obj.card === null) continue;
    const c = printedCharacteristics(game, obj);
    if (hasType(c, CardType.CREATURE) || c.typeLine.subtypes.length > 0) for (const t of c.typeLine.subtypes) out.add(t);
  }
  return out;
}

function spentRiderFits(game: Game, spell: GameObject, payer: PlayerId, read: NonNullable<ReturnType<typeof spentRiderOf>>): boolean {
  const c = characteristicsOf(game, spell);
  if (read.spells === "creature" ? !hasType(c, CardType.CREATURE) : !(hasType(c, CardType.INSTANT) || hasType(c, CardType.SORCERY))) return false;
  if (read.quality === "red" && (c.colors & Color.RED) === 0) return false;
  if (read.quality === "dragon" && !c.typeLine.subtypes.includes("Dragon")) return false;
  if (read.quality === "multicolored" && countBits(c.colors) < 2) return false;
  if (read.sharesCommander) {
    const theirs = commanderCreatureTypes(game, payer);
    if (!c.typeLine.subtypes.some((t) => theirs.has(t))) return false;
  }
  return true;
}

const countBits = (n: number): number => {
  let k = 0;
  for (let x = n; x; x &= x - 1) k += 1;
  return k;
};

/**
 * CR 106.12, 601.2h: mana with a rider was spent on this spell, `amount`
 * units of it. Path of Ancestry: a trigger to scry 1 (each unit is "that
 * mana"); Pyromancer's Goggles, Primal Amulet: a trigger to copy the spell;
 * Boseiju, Who Shelters All: the spell can't be countered; Hall of the
 * Bandit Lord, Carnelian Orb of Dragonkind: it gains haste.
 */
export function manaSpentOn(game: Game, payer: PlayerId, spell: GameObject, rider: ManaRider, amount: number): void {
  const read = spentRiderOf(rider.text);
  if (read === null || !spentRiderFits(game, spell, payer, read)) return;
  if (read.does === "haste") {
    // Hall of the Bandit Lord, Carnelian Orb: the spell and the permanent it becomes
    // (rules/stack.ts carries an effect aimed at the spell onto it), for good or until end of turn.
    const haste = { $: "Ability", kind: STATIC as never, keyword: "Haste", text: "Haste" } as Ability;
    grantToSpell(game, spell, payer, [haste], rider.text, read.untilEndOfTurn ? Duration.END_OF_TURN : Duration.PERMANENT);
    return;
  }
  if (read.does === "can't be countered") {
    grantToSpell(game, spell, payer, [{ $: "Ability", kind: STATIC as never, keyword: "Can't be countered", text: "Can't be countered" }], rider.text);
    return;
  }
  const effect = read.does === "scry"
    ? { $: "Effect", kind: ir("EffectKind.SCRY"), amount: { $: "Value", constant: read.n }, text: `scry ${read.n}` }
    : {
        $: "Effect",
        kind: ir("EffectKind.COPY_SPELL"),
        targets: { $: "ObjectFilter", specific: [spell.id], zones: { $set: [Zone.STACK] } },
        amount: { $: "Value", constant: 1 },
        keywords: [MAY_CHOOSE_NEW_TARGETS],
        text: "copy that spell",
      };
  const ability = {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    trigger: { $: "TriggerCondition", event_kinds: { $set: [EventKind.MANA_SPENT] }, text: "when that mana is spent" },
    effects: [effect],
    text: rider.text,
  } as unknown as Ability;
  for (let i = 0; i < amount; i += 1) {
    game.pendingTriggers.push(pendingTrigger(rider.source, ability, event(EventKind.MANA_SPENT, { player: payer, objectId: spell.id }), payer));
  }
}
