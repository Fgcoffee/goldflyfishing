/**
 * Replacement and prevention effects (CR 614, 615, 616). Ported from
 * mtgfish/rules/cr600_spells_and_abilities/cr614_replacement.py.
 *
 * A replacement effect watches for an event that *would* happen and changes
 * it before it does; nothing is undone, because nothing happened. That is
 * why "if a creature would die, exile it instead" produces no dies trigger.
 *
 * - CR 614.5: a replacement applies at most once to any given event.
 * - CR 616.1: when several could apply, the affected object's controller
 *   (or the affected player) orders them one at a time, re-checking what
 *   still applies after each.
 * - CR 614.15: a self-replacement effect applies before any other.
 *
 * CR 903.9 is split: 903.9b (a commander headed for hand or library) is a
 * replacement and lives here; 903.9a (one in a graveyard or exile) is a
 * state-based action, in sba.ts. A commander that dies really dies first.
 *
 * Importing this module installs it into replacement_hooks.ts.
 */

import type { Ability, Condition, Effect, ObjectFilter, PlayerFilter, TokenSpec } from "../../ir";
import { Zone } from "../../kernel/enums";
import { event, EventKind, replaced, withAmount, type EventKind as EventKindT, type GameEvent } from "../../kernel/events";
import { agentFor, invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { addCounters as putCountersRaw, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { flag, ir, list, num, numSet, str, strs } from "../ir_read";
import { addCounters } from "../cr100_game_concepts/actions";
import { hasKeyword, hasSubtype, charsManaValue } from "../cr200_parts_of_a_card/characteristics";
import { creatureTypesOf } from "../cr200_parts_of_a_card/typeline";
import { chapterNumbers } from "../cr300_card_types/card_types";
import { becomePrepared } from "../cr700_additional_rules/preparation";
import { copyPermanent } from "../cr700_additional_rules/faces";
import { characteristicsOf } from "./layers";
import { installReplacementHooks } from "./replacement_hooks";
import { execute, newResolution } from "./resolve";

/** What a replacement effect does to the event it catches (Python `ReplacementKind`). */
export const ReplacementKind = {
  /** "enters the battlefield tapped" */
  ENTERS_TAPPED: 0,
  /** "enters with N +1/+1 counters on it" */
  ENTERS_WITH_COUNTERS: 1,
  /** "if it would enter, instead ..." - send it somewhere else entirely. */
  REDIRECT_ZONE_CHANGE: 2,
  /** CR 903.9: a commander changing zones may go to the command zone. */
  COMMANDER_ZONE_CHOICE: 3,
  /** Damage: prevent some or all of it (CR 615). */
  PREVENT_DAMAGE: 4,
  /** Damage: change the amount, as doubling or halving effects do. */
  MODIFY_DAMAGE: 5,
  /** Damage: deal it to something else instead (CR 614.9). */
  REDIRECT_DAMAGE: 6,
  /** Counters: add extra, as Doubling Season does. */
  MODIFY_COUNTERS: 7,
  /** Cards: draw a different number, or not at all. */
  MODIFY_DRAW: 8,
  /** The event simply does not happen. */
  PREVENT_ENTIRELY: 9,
  /** Rain of Gore: a life gain becomes a life loss. */
  LIFE_GAIN_BECOMES_LOSS: 10,
  /** Life gain by a different amount. */
  MODIFY_LIFE_CHANGE: 11,
  /** CR 614.12: a self-replacement effect, applied first (CR 616.1). */
  SELF_REPLACEMENT: 12,
  /** CR 614.1: "it creates twice that many instead". */
  MODIFY_TOKENS: 13,
  /** "Lands you control enter untapped": applied last, to beat a tapland's own text. */
  ENTERS_UNTAPPED: 14,
  /** Life loss by a different amount. */
  MODIFY_LIFE_LOSS: 3000,
} as const;
export type ReplacementKind = (typeof ReplacementKind)[keyof typeof ReplacementKind];

/** One active replacement or prevention effect (Python `ReplacementEffect`). */
export interface ReplacementEffect {
  kind: ReplacementKind;
  eventKinds: ReadonlySet<EventKindT>;
  source: ObjectId;
  controller: PlayerId;
  /** Which objects' events this catches; null means the source's own. */
  subject: ObjectFilter | null;
  /** Which players' events this catches. */
  players: PlayerFilter | null;
  /** How much: counters added, damage prevented (-1 for "all"), and so on. */
  amount: number;
  /** Applied before `amount`: "twice that many, plus one". */
  multiplier: number;
  counterType: string;
  /** Where a redirect sends the object. */
  destination: Zone | null;
  /** What a redirect sends damage to. */
  redirectTo: ObjectId;
  /** CR 614.15: applies before every other replacement effect. */
  isSelfReplacement: boolean;
  /** CR 615.5: a shield that is used up once it applies. */
  oneShot: boolean;
  used: boolean;
  /** Damage events: which sources' damage is caught. */
  damageSource: ObjectFilter | null;
  /** CR 615: "dealt to and dealt by" one object. */
  bothWays: boolean;
  /** Counter and token events: the player doing the putting or creating. */
  actor: PlayerFilter | null;
  /** "If an effect would put counters": not a cost's, not CR 714.3b's. */
  effectsOnly: boolean;
  /** Zone changes: where it would have gone, and from where (null: anywhere). */
  watchedToZone: Zone | null;
  watchedFromZone: Zone | null;
  /** A gate on the whole replacement; null is always. */
  condition: Condition | null;
  /** CR 611.2b: how long one made by a resolving spell lasts, and when it began. */
  duration: number;
  createdTurn: number;
  text: string;
}

/** A ReplacementEffect with Python's defaults for everything not given. */
export function newReplacementEffect(
  fields: Partial<ReplacementEffect> & Pick<ReplacementEffect, "kind" | "eventKinds">,
): ReplacementEffect {
  return {
    source: NO_OBJECT,
    controller: NO_PLAYER,
    subject: null,
    players: null,
    amount: 0,
    multiplier: 1,
    counterType: "+1/+1",
    destination: null,
    redirectTo: NO_OBJECT,
    isSelfReplacement: false,
    oneShot: false,
    used: false,
    damageSource: null,
    bothWays: false,
    actor: null,
    effectsOnly: false,
    watchedToZone: null,
    watchedFromZone: null,
    condition: null,
    duration: 0,
    createdTurn: 0,
    text: "",
    ...fields,
  };
}

const describe = (effect: ReplacementEffect): string => effect.text || `replacement ${effect.kind}`;

const EK = {
  REPLACEMENT: ir("EffectKind.REPLACEMENT"),
  PREVENT_DAMAGE: ir("EffectKind.PREVENT_DAMAGE"),
  SEQUENCE: ir("EffectKind.SEQUENCE"),
  CONDITIONAL: ir("EffectKind.CONDITIONAL"),
  UNPARSED: ir("EffectKind.UNPARSED"),
  ENTERS_AS_CHOICE: ir("EffectKind.ENTERS_AS_CHOICE"),
  BECOME_PREPARED: ir("EffectKind.BECOME_PREPARED"),
  TAP: ir("EffectKind.TAP"),
  ADD_COUNTERS: ir("EffectKind.ADD_COUNTERS"),
  COPY_PERMANENT: ir("EffectKind.COPY_PERMANENT"),
};
const STATIC: number = ir("AbilityKind.STATIC");
const ALWAYS: number = ir("ConditionKind.ALWAYS");
const AND: number = ir("ConditionKind.AND");

const isAlways = (c: Condition | null | undefined): boolean => c == null || num(c.kind) === ALWAYS;
const children = (e: Effect): readonly Effect[] => list(e.children);
const otherwise = (e: Effect): readonly Effect[] => list(e.otherwise);

/** Python `Effect.walk`: this effect and every descendant. */
function* walk(effect: Effect): Generator<Effect> {
  yield effect;
  for (const child of [...children(effect), ...otherwise(effect)]) yield* walk(child);
}

// ---------------------------------------------------------------------------
// Applying
// ---------------------------------------------------------------------------

/**
 * Run every applicable replacement effect over an event (CR 614, 616). The
 * modified event, or null if it was replaced out of existence. Each applies
 * at most once (CR 614.5), and the applicable set is re-derived after each
 * application (CR 616.2), since applying one changes what the others see.
 */
export function applyReplacements(game: Game, e: GameEvent): GameEvent | null {
  const applied = new Set<ReplacementEffect>();
  let current = e;
  for (let i = 0; i < 32; i++) {
    const candidates = active(game).filter((r) => !applied.has(r) && applies(game, r, current));
    if (candidates.length === 0) return current;
    // CR 614.15: self-replacement first, then the affected player's choice.
    candidates.sort((a, b) => Number(!a.isSelfReplacement) - Number(!b.isSelfReplacement) || a.source - b.source);
    const chosen = choose(game, current, candidates);
    applied.add(chosen);
    const next = perform(game, chosen, current);
    // A prevention shield of a stated size is spent by amount (CR 615.7), in perform.
    if (chosen.oneShot && chosen.kind !== ReplacementKind.PREVENT_DAMAGE) chosen.used = true;
    if (next === null) return null;
    current = next;
  }
  return current;
}

/**
 * Replacements in force: those a resolution registered, plus those a
 * permanent's static ability supplies - derived, not persisted (CR 611.3).
 */
function active(game: Game): ReplacementEffect[] {
  return [...registered(game).filter((r) => !r.used), ...staticReplacements(game)];
}

const registered = (game: Game): ReplacementEffect[] => game.replacementEffects as ReplacementEffect[];

const staticCache = new WeakMap<Game, { epoch: number; effects: ReplacementEffect[] }>();

/** The replacements the permanents' static abilities supply now (cached per epoch). */
export function staticReplacements(game: Game): ReplacementEffect[] {
  const cached = staticCache.get(game);
  if (cached !== undefined && cached.epoch === game.epoch) return cached.effects;
  const out: ReplacementEffect[] = [];
  for (const id of [...game.battlefield]) {
    const obj = game.objects.get(id);
    if (obj === undefined || obj.phasedOut) continue;
    for (const ability of characteristicsOf(game, obj).abilities) {
      if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
      // Partly read: none of it applies, rather than the half that was.
      if (list(ability.effects).some((e) => num(e.kind) === EK.UNPARSED)) continue;
      const gate = isAlways(ability.static_condition) ? null : (ability.static_condition as Condition);
      for (const [effect, condition] of replacementParts(list(ability.effects), gate)) {
        const built = replacementFromEffect(effect, { source: obj.id, controller: obj.controller, condition });
        if (built !== null) out.push(built);
      }
    }
  }
  staticCache.set(game, { epoch: game.epoch, effects: out });
  return out;
}

const andOf = (a: Condition, b: Condition): Condition => ({ $: "Condition", kind: AND, operands: [a, b], text: "and" } as Condition);

/**
 * The replacement and prevention effects inside a static ability, each with
 * its guard: SEQUENCEs are walked, and a CONDITIONAL with no "otherwise" is
 * a gate on what it wraps (CR 604.1).
 */
function* replacementParts(effects: readonly Effect[], gate: Condition | null): Generator<[Effect, Condition | null]> {
  for (const effect of effects) {
    const kind = num(effect.kind);
    if (kind === EK.REPLACEMENT || kind === EK.PREVENT_DAMAGE) yield [effect, gate];
    else if (kind === EK.SEQUENCE) yield* replacementParts(children(effect), gate);
    else if (kind === EK.CONDITIONAL && otherwise(effect).length === 0) {
      let inner = (effect.condition ?? { $: "Condition" }) as Condition;
      if (gate !== null) inner = andOf(gate, inner);
      yield* replacementParts(children(effect), inner);
    }
  }
}

/** Event kinds that are damage being dealt (CR 120). */
const DAMAGE_EVENTS: ReadonlySet<EventKindT> = new Set([EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT]);

/**
 * The replacement effect an Effect describes, or null. One builder for both
 * places a replacement comes from - a permanent's static ability
 * (CR 614.1, 615.1) and a resolving spell that creates one for a duration
 * (CR 611.2) - so the two can't read the same words differently. Null for a
 * REPLACEMENT the engine has no kind for, which is never registered.
 */
export function replacementFromEffect(
  effect: Effect,
  options: { source: ObjectId; controller: PlayerId; condition?: Condition | null; duration?: number; createdTurn?: number },
): ReplacementEffect | null {
  const common = {
    subject: effect.targets ?? null,
    players: effect.players ?? null,
    damageSource: effect.damage_source ?? null,
    bothWays: flag(effect.both_ways),
    source: options.source,
    controller: options.controller,
    // A stated amount is a shield that is used up (CR 615.7); "all" is -1.
    amount: num(effect.amount?.constant),
    condition: gateOf(options.condition ?? null, effect),
    duration: options.duration ?? 0,
    createdTurn: options.createdTurn ?? 0,
  };
  if (num(effect.kind) === EK.PREVENT_DAMAGE) {
    return newReplacementEffect({
      ...common,
      kind: ReplacementKind.PREVENT_DAMAGE,
      eventKinds: damageEvents(strs(effect.keywords)),
      text: str(effect.text) || "prevent damage",
    });
  }
  const raw = num(effect.replacement_kind);
  if (num(effect.kind) !== EK.REPLACEMENT || !raw) return null;
  if (!(Object.values(ReplacementKind) as number[]).includes(raw)) return null;
  const kind = raw as ReplacementKind;
  let events = eventsFor(kind);
  if (kind === ReplacementKind.MODIFY_DAMAGE || kind === ReplacementKind.PREVENT_DAMAGE) events = damageEvents(strs(effect.keywords));
  const zoneOrNull = (z: unknown): Zone | null => (z === undefined || z === null ? null : (num(z) as Zone));
  return newReplacementEffect({
    ...common,
    kind,
    eventKinds: events,
    actor: effect.actor ?? null,
    effectsOnly: strs(effect.keywords).includes("an effect"),
    multiplier: effect.multiplier === undefined ? 1 : num(effect.multiplier),
    counterType: effect.counter_type === undefined ? "" : str(effect.counter_type),
    destination: zoneOrNull(effect.zone),
    watchedToZone: zoneOrNull(effect.event_zone),
    watchedFromZone: zoneOrNull(effect.from_zone),
    text: str(effect.text),
  });
}

/** The ability's gate and the effect's own condition, together. */
function gateOf(condition: Condition | null, effect: Effect): Condition | null {
  const own = isAlways(effect.condition) ? null : (effect.condition as Condition);
  if (condition === null) return own;
  if (own === null) return condition;
  return andOf(condition, own);
}

/** "combat damage" and "noncombat damage" are narrower than "damage". */
function damageEvents(keywords: readonly string[]): ReadonlySet<EventKindT> {
  if (keywords.includes("combat")) return new Set([EventKind.COMBAT_DAMAGE_DEALT]);
  if (keywords.includes("noncombat")) return new Set([EventKind.DAMAGE_DEALT]);
  return DAMAGE_EVENTS;
}

/** Which events a replacement of this kind watches. */
function eventsFor(kind: ReplacementKind): ReadonlySet<EventKindT> {
  switch (kind) {
    case ReplacementKind.MODIFY_COUNTERS:
      return new Set([EventKind.COUNTER_ADDED]);
    case ReplacementKind.MODIFY_TOKENS:
      return new Set([EventKind.TOKEN_CREATED]);
    case ReplacementKind.MODIFY_DRAW:
      return new Set([EventKind.DREW_CARD]);
    case ReplacementKind.ENTERS_TAPPED:
    case ReplacementKind.ENTERS_UNTAPPED:
    case ReplacementKind.ENTERS_WITH_COUNTERS:
      // A static "creatures your opponents control enter tapped" watches every entry.
      return new Set([EventKind.ENTERS_BATTLEFIELD]);
    case ReplacementKind.MODIFY_DAMAGE:
    case ReplacementKind.PREVENT_DAMAGE:
      return DAMAGE_EVENTS;
    case ReplacementKind.MODIFY_LIFE_CHANGE:
    case ReplacementKind.LIFE_GAIN_BECOMES_LOSS:
      return new Set([EventKind.LIFE_GAINED]);
    case ReplacementKind.MODIFY_LIFE_LOSS:
      return new Set([EventKind.LIFE_LOST]);
    case ReplacementKind.REDIRECT_ZONE_CHANGE:
      return new Set([EventKind.ZONE_CHANGE]);
    default:
      return new Set();
  }
}

interface ReplacementOrderer {
  orderReplacements?(game: Game, affected: PlayerId, e: GameEvent, candidates: ReplacementEffect[]): ReplacementEffect | null;
}

/**
 * CR 616.1: the affected object's controller, or the affected player,
 * chooses - through the agent's `orderReplacements`; without one, the first
 * in the deterministic order, so replays match. "Enters untapped" always
 * goes after "enters tapped", which everyone would choose.
 */
function choose(game: Game, e: GameEvent, candidates: ReplacementEffect[]): ReplacementEffect {
  if (candidates.length > 1 && candidates.some((c) => c.kind === ReplacementKind.ENTERS_UNTAPPED)) {
    const others = candidates.filter((c) => c.kind !== ReplacementKind.ENTERS_UNTAPPED);
    if (others.length) return others[0]!;
  }
  if (candidates.length === 1) return candidates[0]!;
  let affected = e.player;
  if (affected === NO_PLAYER && e.objectId !== NO_OBJECT) {
    const obj = game.objects.get(e.objectId);
    if (obj !== undefined) affected = obj.controller;
  }
  const agent = affected !== NO_PLAYER ? (agentFor(game, affected) as ReplacementOrderer | null | undefined) : null;
  if (agent && typeof agent.orderReplacements === "function") {
    const picked = agent.orderReplacements(game, affected, e, candidates);
    if (picked !== null && candidates.includes(picked)) return picked;
  }
  return candidates[0]!;
}

function applies(game: Game, effect: ReplacementEffect, e: GameEvent): boolean {
  if (!effect.eventKinds.has(e.kind)) return false;
  const hooks = matchHooks();
  if (!isAlways(effect.condition) && !hooks.holds(game, effect.condition!, { source: effect.source, controller: effect.controller })) {
    return false;
  }
  // Each family of event says who is involved in its own fields.
  if (DAMAGE_EVENTS.has(e.kind)) return damageApplies(game, effect, e);
  if (e.kind === EventKind.TOKEN_CREATED) return tokenApplies(game, effect, e);
  if (e.kind === EventKind.COUNTER_ADDED) {
    // "+1/+1 counters" names a kind; an empty kind is any counters.
    if (effect.counterType && (e.data.length === 0 || e.data[0] !== effect.counterType)) return false;
    if (effect.effectsOnly && e.source === NO_OBJECT) return false;
    if (!actorMatches(game, effect, e)) return false;
  }
  if (e.kind === EventKind.ZONE_CHANGE) {
    if (effect.watchedToZone !== null && e.toZone !== effect.watchedToZone) return false;
    if (effect.watchedFromZone !== null && e.fromZone !== effect.watchedFromZone) return false;
  }
  if (effect.subject !== null) {
    const obj = game.objects.get(e.objectId);
    if (obj === undefined) return false;
    if (!hooks.matches(game, obj, effect.subject, { source: effect.source, controller: effect.controller })) return false;
  } else if (
    effect.source !== NO_OBJECT &&
    (effect.kind === ReplacementKind.ENTERS_TAPPED || effect.kind === ReplacementKind.ENTERS_WITH_COUNTERS)
  ) {
    // A self-only replacement applies to its own source and nothing else.
    if (e.objectId !== effect.source) return false;
  }
  if (effect.players !== null && e.player !== NO_PLAYER) {
    if (!hooks.resolvePlayers(game, effect.players, { controller: effect.controller }).includes(e.player)) return false;
  }
  return true;
}

/** CR 120, 614.9, 615: `subject`/`players` are the recipient, `damageSource` the source. */
function damageApplies(game: Game, effect: ReplacementEffect, e: GameEvent): boolean {
  if (effect.bothWays) return recipientMatches(game, effect, e) || sourceMatches(game, effect, effect.subject, e);
  if (!recipientMatches(game, effect, e)) return false;
  return effect.damageSource === null || sourceMatches(game, effect, effect.damageSource, e);
}

/** Who is being dealt the damage: nothing named means anyone. */
function recipientMatches(game: Game, effect: ReplacementEffect, e: GameEvent): boolean {
  const { subject, players } = effect;
  if (subject === null && players === null) return true;
  const hooks = matchHooks();
  if (e.objectId !== NO_OBJECT) {
    // "Damage that would be dealt to you" names a player and no object.
    if (subject === null) return false;
    const obj = game.objects.get(e.objectId);
    if (obj === undefined) return false;
    return hooks.matches(game, obj, subject, { source: effect.source, controller: effect.controller });
  }
  if (e.player === NO_PLAYER) return false;
  if (players !== null) return hooks.resolvePlayers(game, players, { controller: effect.controller }).includes(e.player);
  return flag(subject?.includes_players);
}

/** Whether the damage's source fits `spec`, as it last existed (CR 609.7b). */
function sourceMatches(game: Game, effect: ReplacementEffect, spec: ObjectFilter | null, e: GameEvent): boolean {
  if (spec === null) return true;
  const obj = game.objects.get(e.source);
  if (obj === undefined) return false;
  return matchHooks().matches(game, obj, spec, { source: effect.source, controller: effect.controller, allowStale: true });
}

/** "If you would put ..." - the controller of what is doing it. */
function actorMatches(game: Game, effect: ReplacementEffect, e: GameEvent): boolean {
  if (effect.actor === null) return true;
  const obj = game.objects.get(e.source);
  if (obj === undefined) return false;
  return matchHooks().resolvePlayers(game, effect.actor, { controller: effect.controller }).includes(obj.controller);
}

/**
 * CR 111.1: who the tokens are for, and what they are - read against the
 * TokenSpec in the event's `data`, because none of them exists yet.
 */
function tokenApplies(game: Game, effect: ReplacementEffect, e: GameEvent): boolean {
  if (!actorMatches(game, effect, e)) return false;
  if (effect.players !== null && !matchHooks().resolvePlayers(game, effect.players, { controller: effect.controller }).includes(e.player)) {
    return false;
  }
  if (effect.subject !== null) {
    const spec = (e.data[0] ?? null) as TokenSpec | null;
    if (spec === null || !tokenSpecMatches(spec, effect.subject)) return false;
  }
  return true;
}

/** Whether a token about to be created fits a type description: types, subtypes, colours. */
export function tokenSpecMatches(spec: TokenSpec, wanted: ObjectFilter): boolean {
  const types = spec.types === undefined ? ir("CardType.CREATURE") : num(spec.types);
  const all = num(wanted.types_all);
  const any = num(wanted.types_any);
  const none = num(wanted.types_none);
  if (all && (types & all) !== all) return false;
  if (any && !(types & any)) return false;
  if (none && types & none) return false;
  const subtypes = new Set(strs(spec.subtypes));
  const subAll = strs(wanted.subtypes_all);
  const subAny = strs(wanted.subtypes_any);
  const subNone = strs(wanted.subtypes_none);
  if (subAll.length && !subAll.every((s) => subtypes.has(s))) return false;
  if (subAny.length && !subAny.some((s) => subtypes.has(s))) return false;
  if (subNone.length && subNone.some((s) => subtypes.has(s))) return false;
  const colors = num(spec.colors);
  const cAll = num(wanted.colors_all);
  const cAny = num(wanted.colors_any);
  const cNone = num(wanted.colors_none);
  if (cAll && (colors & cAll) !== cAll) return false;
  if (cAny && !(colors & cAny)) return false;
  return !(cNone && colors & cNone);
}

/** Carry out one replacement; the modified event, or null. */
function perform(game: Game, effect: ReplacementEffect, e: GameEvent): GameEvent | null {
  const kind = effect.kind;
  if (kind === ReplacementKind.PREVENT_ENTIRELY) {
    logRecord(game, `event ${e.kind} is prevented (${describe(effect)})`, { kind: "replace" });
    return null;
  }
  if (kind === ReplacementKind.PREVENT_DAMAGE) {
    const prevented = effect.amount <= 0 ? e.amount : Math.min(e.amount, effect.amount);
    const remaining = e.amount - prevented;
    if (effect.oneShot && effect.amount > 0) {
      // CR 615.7: "the next 3 damage" is a shield of three, spent across events.
      effect.amount -= prevented;
      if (effect.amount <= 0) effect.used = true;
    }
    emit(game, event(EventKind.DAMAGE_PREVENTED, { objectId: e.objectId, player: e.player, source: e.source, amount: prevented }));
    return remaining <= 0 ? null : withAmount(e, remaining);
  }
  if (kind === ReplacementKind.REDIRECT_DAMAGE) return replaced(e, { objectId: effect.redirectTo, player: NO_PLAYER });
  if (
    kind === ReplacementKind.MODIFY_COUNTERS ||
    kind === ReplacementKind.MODIFY_DRAW ||
    kind === ReplacementKind.MODIFY_TOKENS ||
    kind === ReplacementKind.MODIFY_DAMAGE ||
    kind === ReplacementKind.MODIFY_LIFE_CHANGE ||
    kind === ReplacementKind.MODIFY_LIFE_LOSS
  ) {
    // One arithmetic for the family: multiply, then add.
    return withAmount(e, Math.max(0, e.amount * effect.multiplier + effect.amount));
  }
  if (kind === ReplacementKind.LIFE_GAIN_BECOMES_LOSS) return { ...e, kind: EventKind.LIFE_LOST };
  if (kind === ReplacementKind.ENTERS_TAPPED || kind === ReplacementKind.ENTERS_UNTAPPED) {
    const obj = game.objects.get(e.objectId);
    if (obj !== undefined) obj.tapped = kind === ReplacementKind.ENTERS_TAPPED;
    return e;
  }
  if (kind === ReplacementKind.ENTERS_WITH_COUNTERS) {
    const obj = game.objects.get(e.objectId);
    if (obj !== undefined) {
      putCountersRaw(obj, effect.counterType, effect.amount);
      invalidateAllCharacteristics(game);
    }
    return e;
  }
  if (kind === ReplacementKind.REDIRECT_ZONE_CHANGE || kind === ReplacementKind.COMMANDER_ZONE_CHOICE) {
    return effect.destination === null ? e : replaced(e, { toZone: effect.destination });
  }
  return e;
}

// ---------------------------------------------------------------------------
// "As this enters" (CR 614.1c-e)
// ---------------------------------------------------------------------------

/**
 * CR 614.1c: "as this enters" effects from the entering object's own text -
 * self-replacements (CR 614.15) supplied by a permanent that isn't on the
 * battlefield yet, so applied at the moment of entry rather than registered.
 * Taplands enter tapped through here.
 */
export function applySelfEntryReplacements(game: Game, obj: GameObject): void {
  sagaEntersWithLoreCounters(game, obj);
  for (const ability of characteristicsOf(game, obj).abilities) {
    if (num(ability.kind) !== STATIC || flag(ability.unparsed)) continue;
    for (const top of list(ability.effects)) {
      for (const node of walk(top)) {
        const kind = num(node.kind);
        if (kind === EK.COPY_PERMANENT && isEntryCopy(node)) {
          enterAsCopy(game, obj, node);
          continue;
        }
        if (!isSelfEntry(node)) continue;
        if (kind === EK.ENTERS_AS_CHOICE) {
          becomeChosenCharacteristics(game, obj, node);
          continue;
        }
        if (kind === EK.BECOME_PREPARED) becomePrepared(game, obj); // CR 722.3a
        else if (kind === EK.TAP) obj.tapped = true;
        else if (kind === EK.ADD_COUNTERS) {
          // CR 702.54a, 702.176a: only while its condition holds as it enters.
          if (!entryConditionHolds(game, obj, node)) continue;
          let amount = Math.max(1, num(node.amount?.constant));
          // CR 702.38a: N counters for each card revealed.
          if (str(ability.keyword).toLowerCase() === "amplify") amount *= amplifyReveals(game, obj);
          if (amount <= 0) continue;
          addCounters(game, obj, str(node.counter_type) || "+1/+1", amount, { source: obj.id });
        }
      }
    }
  }
}

interface EntryChooser {
  chooseAmplifyReveals?(game: Game, playerId: PlayerId, obj: GameObject, eligible: readonly ObjectId[]): readonly ObjectId[] | null;
  chooseReadAhead?(game: Game, playerId: PlayerId, obj: GameObject, final: number): number;
  chooseEntryOption?(game: Game, playerId: PlayerId, obj: GameObject, options: readonly Effect[]): number;
  chooseCopyTarget?(game: Game, playerId: PlayerId, candidates: readonly GameObject[]): GameObject | null;
}
const entryAgent = (game: Game, playerId: PlayerId) => (agentFor(game, playerId) ?? {}) as EntryChooser;

/**
 * CR 702.38a: how many cards its controller reveals from hand as `obj`
 * enters, each sharing a creature type with it - the agent's
 * `chooseAmplifyReveals`, else every eligible card.
 */
export function amplifyReveals(game: Game, obj: GameObject): number {
  const types = (o: GameObject) => new Set(creatureTypesOf(characteristicsOf(game, o).typeLine).map((n) => n.toLowerCase()));
  const mine = types(obj);
  if (mine.size === 0) return 0;
  const eligible = player(game, obj.controller).hand.filter((id) => {
    if (id === obj.id) return false;
    const card = game.objects.get(id);
    return card !== undefined && [...types(card)].some((t) => mine.has(t));
  });
  if (eligible.length === 0) return 0;
  let revealed = [...eligible];
  const agent = entryAgent(game, obj.controller);
  if (typeof agent.chooseAmplifyReveals === "function") {
    const picked = agent.chooseAmplifyReveals(game, obj.controller, obj, [...eligible]);
    if (picked != null) revealed = eligible.filter((id) => picked.includes(id));
  }
  for (const id of revealed) emit(game, event(EventKind.REVEALED, { objectId: id, player: obj.controller }));
  if (revealed.length) logRecord(game, `amplify: revealed ${revealed.length} card(s)`, { kind: "replace", player: obj.controller });
  return revealed.length;
}

function entryConditionHolds(game: Game, obj: GameObject, node: Effect): boolean {
  if (isAlways(node.condition)) return true;
  return matchHooks().holds(game, node.condition as Condition, { source: obj.id, controller: obj.controller });
}

/**
 * CR 714.3a-b: a Saga's intrinsic "enters with" lore counters - one, or
 * with read ahead a chosen number up to its final chapter (the agent's
 * `chooseReadAhead`; one without it). Put on with addCounters so chapter I
 * triggers (CR 714.2b).
 */
function sagaEntersWithLoreCounters(game: Game, obj: GameObject): void {
  const chars = characteristicsOf(game, obj);
  if (!hasSubtype(chars, "Saga")) return;
  let amount = 1;
  if (hasKeyword(chars, "Read Ahead")) {
    const numbers = chapterNumbers(chars);
    const final = numbers.length ? numbers[numbers.length - 1]! : 0;
    const agent = entryAgent(game, obj.controller);
    if (typeof agent.chooseReadAhead === "function" && final > 1) {
      const picked = agent.chooseReadAhead(game, obj.controller, obj, final);
      if (Number.isInteger(picked) && picked >= 1 && picked <= final) amount = picked;
    }
  }
  addCounters(game, obj, "lore", amount, { source: obj.id });
}

/**
 * CR 208.2b: "it becomes your choice of a 3/3 creature, a 2/2 creature with
 * flying, or ...", as it enters or is turned face up. The controller picks
 * one of `effect.children` (the agent's `chooseEntryOption`; the first
 * without one), and its effects apply to this object from now on.
 *
 * DIVERGENCE (as in Python): CR 208.2b makes the chosen values copiable;
 * here they're continuous effects, so a copy copies the printed card.
 */
export function becomeChosenCharacteristics(game: Game, obj: GameObject, effect: Effect): void {
  const options = children(effect);
  if (options.length === 0) return;
  let index = 0;
  const agent = entryAgent(game, obj.controller);
  if (typeof agent.chooseEntryOption === "function") {
    const picked = agent.chooseEntryOption(game, obj.controller, obj, options);
    if (Number.isInteger(picked) && picked >= 0 && picked < options.length) index = picked;
  }
  execute(newResolution(game, obj.id, obj.controller), [options[index]!]);
  invalidateAllCharacteristics(game);
  logRecord(game, `${characteristicsOf(game, obj).name} becomes ${str(options[index]!.text) || `option ${index + 1}`}`, {
    kind: "replace",
    player: obj.controller,
  });
}

/** "enters as a copy of ...", told apart from a copy on resolution by its text. */
function isEntryCopy(effect: Effect): boolean {
  return str(effect.text).startsWith("enters as a copy");
}

/**
 * CR 614.1c, 706.2: the permanent enters already being something else. The
 * agent's `chooseCopyTarget` (null declines); without one, the biggest
 * candidate by power, then mana value, then id, so replays match. The
 * "except it's a Shapeshifter" tail isn't modelled (the parser consumes it).
 */
function enterAsCopy(game: Game, obj: GameObject, effect: Effect): void {
  const spec = effect.targets ?? null;
  if (spec === null) return;
  const candidates = matchHooks()
    .find(game, spec, { source: obj.id, controller: obj.controller })
    .filter((other) => other.id !== obj.id);
  if (candidates.length === 0) return; // it enters as itself
  const agent = entryAgent(game, obj.controller);
  let chosen: GameObject;
  if (typeof agent.chooseCopyTarget === "function") {
    const picked = agent.chooseCopyTarget(game, obj.controller, candidates);
    if (picked == null) return;
    chosen = picked;
  } else {
    const key = (o: GameObject): [number, number, number] => {
      const c = characteristicsOf(game, o);
      return [c.power ?? 0, charsManaValue(c), o.id];
    };
    chosen = candidates.reduce((best, o) => {
      const [a, b] = [key(o), key(best)];
      return a[0] > b[0] || (a[0] === b[0] && (a[1] > b[1] || (a[1] === b[1] && a[2] > b[2]))) ? o : best;
    });
  }
  copyPermanent(game, obj, chosen);
  invalidateAllCharacteristics(game);
  logRecord(game, `${characteristicsOf(game, obj).name} enters as a copy`, { kind: "replace", player: obj.controller });
}

/** Whether this effect is one of the "as it enters" shapes (by its text, as the parser marks them). */
function isSelfEntry(effect: Effect): boolean {
  const kind = num(effect.kind);
  if (kind === EK.ENTERS_AS_CHOICE) return true;
  if (kind !== EK.TAP && kind !== EK.ADD_COUNTERS && kind !== EK.BECOME_PREPARED) return false;
  // No targets is the effect's own source; the grammar writes an explicit self-filter.
  if (effect.targets != null && !flag(effect.targets.source_only)) return false;
  return str(effect.text).startsWith("enters ");
}

// ---------------------------------------------------------------------------
// Commander rule 903.9b
// ---------------------------------------------------------------------------

/** CR 903.9b applies to these zones only; graveyard and exile are the SBA's (903.9a). */
const REPLACED_COMMANDER_ZONES: ReadonlySet<Zone> = new Set([Zone.HAND, Zone.LIBRARY]);

/** CR 903.9b: a commander headed for its owner's hand or library may go to the command zone. */
export function commanderZoneReplacement(game: Game, e: GameEvent): GameEvent {
  const obj = game.objects.get(e.objectId);
  if (obj === undefined || !obj.isCommander) return e;
  if (e.toZone === null || !REPLACED_COMMANDER_ZONES.has(e.toZone)) return e;
  if (!wantsCommandZone(game, obj, e.toZone)) return e;
  logRecord(game, `${characteristicsOf(game, obj).name} goes to the command zone instead`, { kind: "commander", player: obj.owner });
  emit(game, event(EventKind.COMMANDER_MOVED_TO_COMMAND_ZONE, { objectId: obj.id, player: obj.owner, fromZone: e.fromZone }));
  return replaced(e, { toZone: Zone.COMMAND });
}

/**
 * Whether the commander's owner takes the CR 903.9 option: the agent's
 * `moveCommanderToCommandZone`, else yes - right in nearly every situation.
 */
export function wantsCommandZone(game: Game, obj: GameObject, toZone: Zone): boolean {
  const agent = agentFor(game, obj.owner) as {
    moveCommanderToCommandZone?: (game: Game, owner: PlayerId, obj: GameObject, zone: Zone) => boolean;
  } | null | undefined;
  if (agent && typeof agent.moveCommanderToCommandZone === "function") {
    return Boolean(agent.moveCommanderToCommandZone(game, obj.owner, obj, toZone));
  }
  return true;
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

/** Register a replacement a resolving spell or ability created (CR 611.2). */
export function register(game: Game, effect: ReplacementEffect): ReplacementEffect {
  game.replacementEffects.push(effect);
  return effect;
}

/** Drop the used-up ones. */
export function clearExpired(game: Game): void {
  game.replacementEffects = registered(game).filter((r) => !r.used);
}

/** Python `Game.has_replacements`: any registered or static replacement in force. */
export function hasReplacements(game: Game): boolean {
  return registered(game).some((r) => !r.used) || staticReplacements(game).length > 0;
}

/** Install into replacement_hooks.ts. Done on import; callable again (tests). */
export function installReplacements(): void {
  installReplacementHooks({
    hasReplacements: (game) => hasReplacements(game),
    replace: (game, e) => (hasReplacements(game) ? applyReplacements(game, e) : e),
    commanderZoneReplacement: (game, e) => commanderZoneReplacement(game, e),
    applySelfEntryReplacements: (game, obj) => applySelfEntryReplacements(game, obj),
  });
}

installReplacements();
