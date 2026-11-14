/**
 * The layer system (CR 613): an object's characteristics after continuous
 * effects. Ported from mtgfish/rules/cr600_spells_and_abilities/cr613_layers.py,
 * with the memoising front doors from mtgfish/rules/kernel/game.py
 * (`characteristics`, `printed_characteristics`, `board`).
 *
 * Computed for the whole board at once, because CR 613.6 makes each layer
 * depend on the previous one across every object. Effects from resolved spells
 * persist (CR 611.2); effects from static abilities are regenerated from the
 * abilities that exist right now (CR 611.3, 604.2).
 *
 * Matching, values and conditions go through kernel/hooks.ts (rules/match/).
 */

import type { Ability, Effect, Value } from "../../ir";
import { CardType, Color, Layer, Layout, Supertype, Zone } from "../../kernel/enums";
import type { CardTypeLine, FaceDef } from "../../kernel/card";
import { printedNumber } from "../../kernel/card";
import type { ContinuousEffect, Game } from "../../kernel/game";
import { isLive, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import { flag, ir, irEqual, irIncludes, isEnum, list, num, numSet, str, strs } from "../ir_read";
import { manaValue } from "../cr100_game_concepts/mana_value";
import {
  abilityKeyword,
  characteristics as makeCharacteristics,
  isCreature,
  replaceCharacteristics,
  type Characteristics,
} from "../cr200_parts_of_a_card/characteristics";
import { perpetualContinuousEffects, perpetuallyChanged } from "../cr700_additional_rules/digital_mechanics";
import { activeRegistry, addingTypes, removingTypes, type TypeLine } from "../cr200_parts_of_a_card/typeline";

// IR enum numbers this module dispatches on (EffectKind, AbilityKind, ValueKind).
const EK = {
  SEQUENCE: 0,
  EXILE: 30,
  GAIN_CONTROL: 77,
  COPY_PERMANENT: 84,
  CAST_WITHOUT_PAYING: 103,
  ADD_MANA: 120,
  MODIFY_PT: 140,
  SET_PT: 141,
  SWITCH_PT: 142,
  GRANT_ABILITY: 143,
  REMOVE_ABILITIES: 144,
  SET_COLOR: 145,
  ADD_TYPE: 146,
  REMOVE_TYPE: 147,
  SET_TYPE: 148,
  TEXT_CHANGE: 149,
} as const;
const AK = { SPELL: 0, ACTIVATED: 1, TRIGGERED: 2, STATIC: 3 } as const;

/** Which layer each continuous effect kind applies in (CR 613.1). */
export const EFFECT_LAYERS: ReadonlyMap<number, Layer> = new Map([
  [EK.COPY_PERMANENT, Layer.COPY],
  [EK.TEXT_CHANGE, Layer.TEXT],
  [EK.ADD_TYPE, Layer.TYPE],
  [EK.REMOVE_TYPE, Layer.TYPE],
  [EK.SET_TYPE, Layer.TYPE],
  [EK.SET_COLOR, Layer.COLOR],
  [EK.GRANT_ABILITY, Layer.ABILITY],
  [EK.REMOVE_ABILITIES, Layer.ABILITY],
  [EK.SET_PT, Layer.PT_SET],
  [EK.MODIFY_PT, Layer.PT_MODIFY],
  [EK.SWITCH_PT, Layer.PT_SWITCH],
]);

/** CONTINUOUS_KINDS from effects.py: the effects a static ability applies. */
const CONTINUOUS_KINDS: ReadonlySet<number> = new Set([...EFFECT_LAYERS.keys(), EK.GAIN_CONTROL]);

export const LAYER_ORDER: readonly Layer[] = [
  Layer.COPY,
  Layer.FACE_DOWN,
  Layer.CONTROL,
  Layer.TEXT,
  Layer.TYPE,
  Layer.COLOR,
  Layer.ABILITY,
  Layer.PT_CDA,
  Layer.PT_SET,
  Layer.PT_MODIFY,
  Layer.PT_COUNTERS,
  Layer.PT_SWITCH,
];

export const BASIC_LAND_MANA: ReadonlyMap<string, Color> = new Map([
  ["Plains", Color.WHITE],
  ["Island", Color.BLUE],
  ["Swamp", Color.BLACK],
  ["Mountain", Color.RED],
  ["Forest", Color.GREEN],
]);

const TRANSFORMING_LAYOUTS: ReadonlySet<Layout> = new Set([Layout.TRANSFORM, Layout.BATTLE]);

function kindOf(effect: Effect): number {
  return num(effect.kind, 5);
}

export function layerFor(effect: Effect): Layer {
  return EFFECT_LAYERS.get(kindOf(effect)) ?? Layer.ABILITY;
}

// ---------------------------------------------------------------------------
// Front doors (kernel/game.py)
// ---------------------------------------------------------------------------

type Board = Map<ObjectId, Characteristics>;

/** The whole-board computation, memoised against `game.epoch`. */
export function board(game: Game): Board {
  if (game.boardInProgress !== null) return game.boardInProgress;
  if (game.boardCache !== null && game.boardEpoch === game.epoch) return game.boardCache;
  const computed = computeBoard(game);
  game.boardCache = computed;
  game.boardEpoch = game.epoch;
  return computed;
}

/** An object's current characteristics, after continuous effects. */
export function characteristicsOf(game: Game, obj: GameObject): Characteristics {
  if (game.boardInProgress !== null) {
    const found = game.boardInProgress.get(obj.id);
    if (found !== undefined) return found;
    if (obj.faceDown && isHidden(obj)) return hiddenCharacteristics();
    return printedCharacteristics(game, obj);
  }
  if (obj.cachedCharacteristics !== null && obj.characteristicsEpoch === game.epoch) {
    return obj.cachedCharacteristics;
  }
  const result = computeCharacteristics(game, obj);
  obj.cachedCharacteristics = result;
  obj.characteristicsEpoch = game.epoch;
  return result;
}

/** Characteristics before any continuous effect: the copiable values (CR 613.2). */
export function printedCharacteristics(game: Game, obj: GameObject): Characteristics {
  const key = `${obj.faceIndex}|${obj.flipped ? 1 : 0}|${obj.faceDown ? 1 : 0}|${game.abilityProvider.generation ?? 0}`;
  if (obj.printed !== null && obj.printedKey === key) return obj.printed;
  const printed = buildPrinted(game, obj);
  obj.printed = printed;
  obj.printedKey = key;
  return printed;
}

/** A card, or a token's synthetic card carrying its own abilities. */
interface CardShape {
  readonly name?: string;
  readonly layout?: Layout;
  readonly faces?: readonly FaceDef[];
  readonly abilities?: readonly Ability[];
  readonly face_abilities?: readonly (readonly Ability[])[];
}

function buildPrinted(game: Game, obj: GameObject): Characteristics {
  const card = obj.card as CardShape | null;
  if (card === null || card === undefined) return makeCharacteristics();
  // CR 710.1c: an unflipped flip card reads its top half; flipped, the bottom.
  const faceIndex = obj.flipped && obj.faceIndex === 0 ? 1 : obj.faceIndex;
  const faces = card.faces ?? [];
  // KNOWN: CR 718.2 prototype faces (Python card_face/prototype_face) are not
  // ported yet; a prototyped object reads no face here.
  const face = faces[faceIndex];
  if (face === undefined) return makeCharacteristics({ name: card.name ?? "" });
  // Tokens and emblems carry their abilities; cards get them from the IR.
  const perFace = card.face_abilities ?? [];
  let abilities: readonly Ability[];
  if (faceIndex < perFace.length) abilities = perFace[faceIndex] ?? [];
  else if (card.abilities !== undefined) abilities = card.abilities;
  else abilities = game.abilityProvider.abilitiesFor(card, faceIndex);
  let printed = fromFace(face, [...abilities, ...intrinsicAbilities(face.type_line)]);
  if (faceIndex !== obj.faceIndex) {
    const top = faces[obj.faceIndex];
    if (top !== undefined) {
      printed = replaceCharacteristics(printed, {
        manaCost: top.mana_cost,
        hasManaCost: top.has_mana_cost,
        colors: top.color_indicator ?? top.colors,
      });
    }
  } else if (faceIndex === 1 && card.layout !== undefined && TRANSFORMING_LAYOUTS.has(card.layout)) {
    // CR 712.8c, 712.8e: transformed, it keeps its front face's mana value.
    const front = faces[0];
    if (front !== undefined) printed = replaceCharacteristics(printed, { manaValueFromFront: manaValue(front.mana_cost) });
  }
  return printed;
}

/**
 * The printed characteristics of a face (characteristics.py `from_face`).
 * CR 208.2: a printed "*" is null, a signal for a characteristic-defining
 * ability, never zero. A colour indicator is the whole colour (CR 204.2).
 */
export function fromFace(face: FaceDef, abilities: readonly Ability[] = []): Characteristics {
  return makeCharacteristics({
    name: face.name,
    manaCost: face.mana_cost,
    hasManaCost: face.has_mana_cost,
    colors: face.color_indicator ?? face.colors,
    typeLine: face.type_line,
    abilities,
    power: printedNumber(face.power),
    toughness: printedNumber(face.toughness),
    loyalty: printedNumber(face.loyalty),
    defense: printedNumber(face.defense),
    text: face.oracle_text,
  });
}

// ---------------------------------------------------------------------------
// Face-down objects (cr708_face_down.py)
// ---------------------------------------------------------------------------

/** What turned it face down, when that lists ward {2} (CR 702.168a, 701.58a). */
const WARDED: ReadonlySet<string> = new Set(["Disguise", "Cloak"]);
const FACE_DOWN_BODY_ZONES: ReadonlySet<Zone> = new Set([Zone.BATTLEFIELD, Zone.STACK]);

/** CR 708.2: a face-down permanent is a nameless 2/2 creature. */
export function faceDownCharacteristics(obj: GameObject): Characteristics {
  return makeCharacteristics({
    typeLine: { supertypes: Supertype.NONE, types: CardType.CREATURE, subtypes: [] },
    abilities: WARDED.has(obj.faceDownBy) ? [WARD_TWO] : [],
    power: 2,
    toughness: 2,
  });
}

export function hiddenCharacteristics(): Characteristics {
  return makeCharacteristics();
}

export function isHidden(obj: GameObject): boolean {
  return obj.faceDown && !FACE_DOWN_BODY_ZONES.has(obj.zone);
}

const WARD_TWO: Ability = {
  $: "Ability",
  kind: ir("AbilityKind.STATIC"),
  keyword: "Ward",
  text: "Ward {2}",
  cost: {
    $: "Cost",
    components: [
      { $: "CostComponent", kind: ir("CostKind.MANA"), mana: { $: "ManaCost", symbols: [{ $: "ManaSymbol", kind: ir("ManaSymbolKind.GENERIC"), generic: 2 }] } },
    ],
  },
};

// ---------------------------------------------------------------------------
// The board computation
// ---------------------------------------------------------------------------

/** One object's characteristics, from the whole-board computation. */
export function computeCharacteristics(game: Game, obj: GameObject): Characteristics {
  if (obj.zone !== Zone.BATTLEFIELD && obj.zone !== Zone.STACK) {
    if (obj.faceDown) return hiddenCharacteristics();
    const reached = board(game).get(obj.id);
    if (reached !== undefined) return reached;
    return applyCda(game, obj, printedCharacteristics(game, obj));
  }
  return board(game).get(obj.id) ?? printedCharacteristics(game, obj);
}

type Grants = Map<ObjectId, [Ability, number][]>;

/**
 * Characteristics of every object continuous effects can touch: the
 * battlefield, the stack (CR 610.5), emblems, and objects elsewhere that an
 * effect names (CR 611.2c, 611.3a).
 */
export function computeBoard(game: Game): Board {
  const scope: GameObject[] = [];
  for (const id of game.battlefield) pushIfPresent(game, scope, id);
  for (const id of game.stack) pushIfPresent(game, scope, id);
  for (const id of game.command) {
    const obj = game.objects.get(id);
    if (obj !== undefined && obj.kind === ObjectKind.EMBLEM) scope.push(obj);
  }
  scope.push(...reachedElsewhere(game, scope));

  const state: Board = new Map();
  for (const obj of scope) state.set(obj.id, printedCharacteristics(game, obj));
  const byId = new Map<ObjectId, GameObject>(scope.map((o) => [o.id, o]));
  const previous = game.boardInProgress;
  game.boardInProgress = state;
  try {
    let byLayer = groupByLayer(liveEffects(game, state, byId));
    const grants: Grants = new Map();
    for (const layer of LAYER_ORDER) {
      applyLayer(game, layer, state, byId, byLayer.get(layer) ?? [], grants);
      if (layer === Layer.PT_SET) {
        for (const [id, chars] of state) {
          state.set(id, replaceCharacteristics(chars, {
            basePowerAfterSetting: chars.power,
            baseToughnessAfterSetting: chars.toughness,
          }));
        }
      }
      if (layer === Layer.ABILITY) byLayer = groupByLayer(liveEffects(game, state, byId, grants));
    }
    for (const [id, obj] of byId) state.set(id, settleTypeCharacteristics(obj, state.get(id) as Characteristics));
  } finally {
    game.boardInProgress = previous;
  }
  return state;
}

function pushIfPresent(game: Game, out: GameObject[], id: ObjectId): void {
  const obj = game.objects.get(id);
  if (obj !== undefined) out.push(obj);
}

/**
 * cr300_characteristics.py `settle_type_characteristics`: CR 208.3, a
 * noncreature permanent has no power or toughness (and CR 301.7a, a Vehicle
 * that isn't a creature).
 */
export function settleTypeCharacteristics(obj: GameObject, chars: Characteristics): Characteristics {
  if (obj.zone !== Zone.BATTLEFIELD || isCreature(chars)) return chars;
  if (chars.power === null && chars.toughness === null) return chars;
  return replaceCharacteristics(chars, { power: null, toughness: null });
}

const ELSEWHERE: readonly Zone[] = [Zone.GRAVEYARD, Zone.EXILE, Zone.HAND, Zone.LIBRARY];

function reachedElsewhere(game: Game, scope: readonly GameObject[]): GameObject[] {
  const seen = new Set(scope.map((o) => o.id));
  const out: GameObject[] = [];
  const admit = (obj: GameObject | undefined): void => {
    if (obj === undefined || seen.has(obj.id) || !isLive(obj)) return;
    if (!ELSEWHERE.includes(obj.zone)) return;
    seen.add(obj.id);
    out.push(obj);
  };
  const zones = new Set<Zone>();
  for (const ce of game.continuousEffects) {
    const effect = ce.effect as Effect;
    const spec = effect.targets;
    if (ce.expired || spec === undefined || spec === null || !EFFECT_LAYERS.has(kindOf(effect))) continue;
    const specZones = numSet(spec.zones, [Zone.BATTLEFIELD]);
    const specific = list(spec.specific);
    if (specific.length > 0) {
      for (const id of specific) {
        const obj = game.objects.get(id);
        if (obj !== undefined && (specZones.size === 0 || specZones.has(obj.zone))) admit(obj);
      }
    } else {
      for (const z of specZones) if (ELSEWHERE.includes(z)) zones.add(z);
    }
  }
  for (const obj of scope) {
    if (obj.zone !== Zone.BATTLEFIELD && obj.kind !== ObjectKind.EMBLEM) continue;
    for (const ability of printedCharacteristics(game, obj).abilities) {
      if (num(ability.kind) !== AK.STATIC || flag(ability.unparsed)) continue;
      for (const effect of continuousParts(list(ability.effects))) {
        if (effect.targets && EFFECT_LAYERS.has(kindOf(effect))) {
          for (const z of numSet(effect.targets.zones, [Zone.BATTLEFIELD])) if (ELSEWHERE.includes(z)) zones.add(z);
        }
      }
    }
  }
  for (const zone of [...zones].sort((a, b) => a - b)) {
    if (zone === Zone.EXILE) {
      for (const id of game.exile) admit(game.objects.get(id));
      continue;
    }
    for (const p of game.players) {
      const ids = zone === Zone.LIBRARY ? p.library : zone === Zone.HAND ? p.hand : p.graveyard;
      for (const id of ids) admit(game.objects.get(id));
    }
  }
  // MTG Arena's "perpetually": a card changed perpetually carries the
  // change into every zone, so it is computed wherever it is.
  for (const obj of perpetuallyChanged(game)) admit(obj);
  return out;
}

function applyLayer(
  game: Game,
  layer: Layer,
  state: Board,
  byId: Map<ObjectId, GameObject>,
  effects: readonly ContinuousEffect[],
  grants: Grants | null,
): void {
  if (layer === Layer.CONTROL) {
    applyControl(game, state, byId);
    return;
  }
  if (layer === Layer.FACE_DOWN) {
    for (const [id, obj] of byId) if (obj.faceDown) state.set(id, faceDownCharacteristics(obj));
    return;
  }
  if (layer === Layer.TYPE) applyBestowed(state, byId);
  if (layer === Layer.ABILITY) {
    for (const [id, obj] of byId) state.set(id, applyKeywordCounters(obj, state.get(id) as Characteristics));
  }
  if (layer === Layer.PT_CDA) {
    for (const [id, obj] of byId) state.set(id, applyCda(game, obj, state.get(id) as Characteristics));
    return;
  }
  if (layer === Layer.PT_COUNTERS) {
    for (const [id, obj] of byId) state.set(id, applyCounters(obj, state.get(id) as Characteristics));
    return;
  }
  if (effects.length === 0) return;
  const ordered = [...byId.keys()].sort((a, b) => a - b);
  for (const ce of orderEffects(game, effects, state, byId)) {
    if (!stillExists(state, ce)) continue;
    const records = grants !== null && kindOf(ce.effect as Effect) === EK.GRANT_ABILITY;
    for (const id of ordered) {
      const obj = byId.get(id) as GameObject;
      const before = state.get(id) as Characteristics;
      if (!affects(game, ce, obj, before)) continue;
      const after = applyEffect(game, obj, before, ce);
      state.set(id, after);
      if (records) recordGrant(grants as Grants, id, before, after, ce.timestamp);
    }
  }
}

/** CR 613.7a: note which abilities a grant added, and when. The latest wins. */
function recordGrant(grants: Grants, id: ObjectId, before: Characteristics, after: Characteristics, timestamp: number): void {
  let kept = grants.get(id);
  if (kept === undefined) {
    kept = [];
    grants.set(id, kept);
  }
  for (const ability of after.abilities.slice(before.abilities.length)) {
    const index = kept.findIndex(([known]) => irEqual(known, ability));
    if (index < 0) kept.push([ability, timestamp]);
    else if (timestamp > (kept[index] as [Ability, number])[1]) kept[index] = [ability, timestamp];
  }
}

function grantedTimestamp(grants: Grants | null, id: ObjectId, ability: Ability): number {
  if (grants === null) return 0;
  for (const [known, when] of grants.get(id) ?? []) if (irEqual(known, ability)) return when;
  return 0;
}

/** Whether the static ability generating this effect is still present. */
function stillExists(state: Board, ce: ContinuousEffect): boolean {
  if (!ce.fromStaticAbility || ce.ability === null) return true;
  const chars = state.get(ce.source);
  return chars !== undefined && irIncludes(chars.abilities, ce.ability);
}

function groupByLayer(effects: readonly ContinuousEffect[]): Map<number, ContinuousEffect[]> {
  const out = new Map<number, ContinuousEffect[]>();
  for (const ce of effects) {
    const bucket = out.get(ce.layer);
    if (bucket) bucket.push(ce);
    else out.set(ce.layer, [ce]);
  }
  return out;
}

/**
 * Continuous effects in existence: those from resolved spells (CR 611.2),
 * and those regenerated from static abilities that exist now and function
 * where their object is (CR 611.3, 604.2).
 */
function liveEffects(game: Game, state: Board, byId: Map<ObjectId, GameObject>, grants: Grants | null = null): ContinuousEffect[] {
  const out = game.continuousEffects.filter((ce) => !ce.expired && EFFECT_LAYERS.has(kindOf(ce.effect as Effect)));
  // MTG Arena's "perpetually": each card's perpetual changes, to that card alone, in every zone.
  out.push(...perpetualContinuousEffects(game, byId));
  for (const [id, obj] of byId) {
    if (obj.phasedOut) continue;
    if (obj.zone !== Zone.BATTLEFIELD && !(obj.zone === Zone.COMMAND && obj.kind === ObjectKind.EMBLEM)) continue;
    for (const ability of (state.get(id) as Characteristics).abilities) {
      if (num(ability.kind) !== AK.STATIC || flag(ability.unparsed)) continue;
      if (!staticConditionHolds(game, obj, ability)) continue;
      const timestamp = Math.max(obj.timestamp, grantedTimestamp(grants, id, ability));
      for (const effect of continuousParts(list(ability.effects))) {
        out.push({
          effect,
          source: obj.id,
          controller: obj.controller,
          timestamp,
          layer: layerFor(effect),
          duration: num(effect.duration),
          createdTurn: 0,
          ability,
          fromStaticAbility: true,
          isCda: flag(ability.is_characteristic_defining),
          expired: false,
        });
      }
    }
  }
  return out;
}

/** The continuous effects inside a static ability, sequences opened up. */
function continuousParts(effects: readonly Effect[]): Effect[] {
  const found: Effect[] = [];
  const stack = [...effects];
  while (stack.length > 0) {
    const effect = stack.shift() as Effect;
    const kind = kindOf(effect);
    if (CONTINUOUS_KINDS.has(kind)) found.push(effect);
    else if (kind === EK.SEQUENCE) stack.unshift(...list(effect.children));
  }
  return found;
}

function staticConditionHolds(game: Game, obj: GameObject, ability: Ability): boolean {
  const condition = ability.static_condition;
  if (condition === undefined || num(condition.kind) === 0) return true;
  return matchHooks().holds(game, condition, { source: obj.id, controller: obj.controller });
}

/**
 * Whether a continuous effect applies to this object, given `chars`. Asked
 * afresh every time (CR 604.4); a static ability never reaches last-known
 * information (CR 604.7), so no allowStale.
 */
function affects(game: Game, ce: ContinuousEffect, obj: GameObject, chars: Characteristics): boolean {
  const effect = ce.effect as Effect;
  const spec = effect.targets;
  if (spec === undefined || spec === null) return obj.id === ce.source;
  if (obj.phasedOut || !EFFECT_LAYERS.has(kindOf(effect))) return false;
  return matchHooks().matches(game, obj, spec, { source: ce.source, controller: ce.controller, override: chars });
}

/**
 * Order one layer's effects (CR 613.7, 613.8): CDAs first, then timestamp;
 * an effect that depends on another comes after it; a dependency loop falls
 * back to timestamp order.
 */
export function orderEffects(game: Game, effects: readonly ContinuousEffect[], state: Board, byId: Map<ObjectId, GameObject>): ContinuousEffect[] {
  const ordered = [...effects].sort(
    (a, b) => (a.isCda ? 0 : 1) - (b.isCda ? 0 : 1) || a.timestamp - b.timestamp || a.source - b.source,
  );
  if (ordered.length < 2) return ordered;
  const count = ordered.length;
  const probe = new DependencyProbe(game, ordered, state, byId);
  const depends: Set<number>[] = ordered.map(() => new Set());
  for (let i = 0; i < count; i += 1) {
    for (let j = 0; j < count; j += 1) if (i !== j && probe.dependsOn(i, j)) depends[i]?.add(j);
  }
  if (depends.every((d) => d.size === 0)) return ordered;
  const remaining = new Set(ordered.map((_, i) => i));
  const out: ContinuousEffect[] = [];
  while (remaining.size > 0) {
    let ready = [...remaining].filter((i) => ![...(depends[i] as Set<number>)].some((j) => remaining.has(j)));
    if (ready.length === 0) ready = [...remaining];
    // The earliest by (timestamp, source, position), as Python's min(key=...).
    const key = (i: number): [number, number, number] => {
      const ce = ordered[i] as ContinuousEffect;
      return [ce.timestamp, ce.source, i];
    };
    const before = (x: number, y: number): boolean => {
      const [a, b] = [key(x), key(y)];
      return (a[0] - b[0] || a[1] - b[1] || a[2] - b[2]) < 0;
    };
    const chosen = ready.reduce((best, i) => (before(i, best) ? i : best));
    out.push(ordered[chosen] as ContinuousEffect);
    remaining.delete(chosen);
  }
  return out;
}

/**
 * CR 613.8a, tried rather than hard-coded: apply B, then ask whether A still
 * exists, still applies to the same objects, and still does the same thing.
 * Each effect's footprint is worked out once per layer.
 */
class DependencyProbe {
  private readonly ids: ObjectId[];
  private readonly footprints = new Map<number, Map<ObjectId, Characteristics>>();
  private readonly applies = new Map<string, boolean>();
  private readonly ops = new Map<string, string>();

  constructor(
    private readonly game: Game,
    private readonly effects: readonly ContinuousEffect[],
    private readonly state: Board,
    private readonly byId: Map<ObjectId, GameObject>,
  ) {
    this.ids = [...byId.keys()].sort((a, b) => a - b);
  }

  footprint(index: number): Map<ObjectId, Characteristics> {
    let found = this.footprints.get(index);
    if (found === undefined) {
      const effect = this.effects[index] as ContinuousEffect;
      found = new Map();
      for (const id of this.ids) {
        const obj = this.byId.get(id) as GameObject;
        const base = this.state.get(id) as Characteristics;
        if (affects(this.game, effect, obj, base)) found.set(id, applyEffect(this.game, obj, base, effect));
      }
      this.footprints.set(index, found);
    }
    return found;
  }

  private appliesBefore(index: number, id: ObjectId): boolean {
    const key = `${index}|${id}`;
    let known = this.applies.get(key);
    if (known === undefined) {
      known = affects(this.game, this.effects[index] as ContinuousEffect, this.byId.get(id) as GameObject, this.state.get(id) as Characteristics);
      this.applies.set(key, known);
    }
    return known;
  }

  private operationBefore(index: number, id: ObjectId): string {
    const key = `${index}|${id}`;
    let known = this.ops.get(key);
    if (known === undefined) {
      known = operation(this.game, this.byId.get(id) as GameObject, this.state.get(id) as Characteristics, this.effects[index] as ContinuousEffect);
      this.ops.set(key, known);
    }
    return known;
  }

  dependsOn(i: number, j: number): boolean {
    const a = this.effects[i] as ContinuousEffect;
    const b = this.effects[j] as ContinuousEffect;
    if (a === b || a.isCda !== b.isCda) return false;
    const footprint = this.footprint(j);
    if (a.fromStaticAbility && a.ability !== null) {
      const sourceChars = this.state.get(a.source);
      const after = footprint.get(a.source);
      if (
        this.game.objects.has(a.source) &&
        sourceChars !== undefined &&
        irIncludes(sourceChars.abilities, a.ability) &&
        after !== undefined &&
        !irIncludes(after.abilities, a.ability)
      ) {
        return true;
      }
    }
    for (const [id, afterB] of footprint) {
      const obj = this.byId.get(id) as GameObject;
      const before = this.appliesBefore(i, id);
      if (before !== affects(this.game, a, obj, afterB)) return true;
      if (before && this.operationBefore(i, id) !== operation(this.game, obj, afterB, a)) return true;
    }
    return false;
  }
}

/** What an effect does, independent of what it does it to (CR 613.8a). */
function operation(game: Game, obj: GameObject, _chars: Characteristics, ce: ContinuousEffect): string {
  const effect = ce.effect as Effect;
  return JSON.stringify([
    kindOf(effect),
    valueOf(game, effect.amount, ce, obj),
    valueOf(game, effect.amount2, ce, obj),
    num(effect.types),
    num(effect.colors),
    strs(effect.keywords),
    str(effect.counter_type),
  ]);
}

function isUnchanged(value: Value | undefined): boolean {
  return value !== undefined && isEnum(value.kind, "ValueKind.UNCHANGED");
}

function isConstant(value: Value): boolean {
  return isEnum(value.kind, "ValueKind.CONSTANT");
}

/** Apply one continuous effect to a characteristic set. */
export function applyEffect(game: Game, obj: GameObject, current: Characteristics, ce: ContinuousEffect): Characteristics {
  const effect = ce.effect as Effect;
  switch (kindOf(effect)) {
    case EK.MODIFY_PT:
      return replaceCharacteristics(current, {
        power: (current.power ?? 0) + valueOf(game, effect.amount, ce, obj),
        toughness: (current.toughness ?? 0) + valueOf(game, effect.amount2, ce, obj),
      });
    case EK.SET_PT:
      return replaceCharacteristics(current, {
        power: isUnchanged(effect.amount) ? current.power : valueOf(game, effect.amount, ce, obj),
        toughness: isUnchanged(effect.amount2) ? current.toughness : valueOf(game, effect.amount2, ce, obj),
      });
    case EK.SWITCH_PT:
      return replaceCharacteristics(current, { power: current.toughness, toughness: current.power });
    case EK.SET_COLOR:
      return replaceCharacteristics(current, { colors: num(effect.colors) });
    case EK.TEXT_CHANGE:
      return applyTextChange(current, effect);
    case EK.ADD_TYPE: {
      let subtypes = strs(effect.keywords);
      if (flag(effect.of_chosen_type)) {
        const chosen = game.objects.get(ce.source)?.chosenType ?? "";
        subtypes = chosen ? [chosen] : [];
      }
      const line = addingTypes(current.typeLine, { supertypes: num(effect.supertypes), types: num(effect.types), subtypes });
      return withTypeLine(current, line);
    }
    case EK.REMOVE_TYPE:
      return withTypeLine(current, removingTypes(current.typeLine, { types: num(effect.types), subtypes: strs(effect.keywords) }));
    case EK.SET_TYPE: {
      const types = num(effect.types);
      const words = strs(effect.keywords);
      const setsBasicLandType = (types & CardType.LAND) !== 0 && words.some((s) => BASIC_LAND_MANA.has(s));
      let removed: readonly string[];
      let stripped: Characteristics;
      if (setsBasicLandType) {
        removed = current.typeLine.subtypes.filter(isLandType);
        stripped = replaceCharacteristics(current, { abilities: [] });
      } else {
        removed = current.typeLine.subtypes;
        stripped = replaceCharacteristics(current, { abilities: current.abilities.filter((a) => !flag(a.from_land_type)) });
      }
      let line = removingTypes(current.typeLine, { subtypes: removed });
      line = addingTypes(line, { types, subtypes: words });
      return withTypeLine(stripped, line);
    }
    case EK.GRANT_ABILITY: {
      const granted = [...list(effect.granted_abilities), ...strs(effect.keywords).map(keywordAbility)];
      return replaceCharacteristics(current, { abilities: [...current.abilities, ...granted] });
    }
    case EK.REMOVE_ABILITIES: {
      const names = new Set(strs(effect.keywords).map((n) => n.toLowerCase()));
      if (names.size > 0 && !names.has("*")) {
        return replaceCharacteristics(current, { abilities: current.abilities.filter((a) => !names.has(abilityKeyword(a).toLowerCase())) });
      }
      return replaceCharacteristics(current, { abilities: [] });
    }
    case EK.COPY_PERMANENT: {
      const source = game.objects.get(num(effect.copy_source));
      return source === undefined ? current : copiableCharacteristics(game, source);
    }
    default:
      return current;
  }
}

/** A bare keyword ability, as granted by name. */
export function keywordAbility(word: string): Ability {
  return { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: word, text: word };
}

/**
 * cr707_faces.py `copiable_characteristics`: printed characteristics of the
 * face up, as modified by copy effects only (CR 707.2, 613.2); a face-down
 * permanent's are the 2/2's (CR 708.2).
 */
export function copiableCharacteristics(game: Game, obj: GameObject): Characteristics {
  if (obj.faceDown) return faceDownCharacteristics(obj);
  const printed = printedCharacteristics(game, obj);
  return printed.manaValueFromFront === null ? printed : replaceCharacteristics(printed, { manaValueFromFront: null });
}

/** Layer 3 (CR 612): from/to subtype pairs in `keywords`; subtypes only (CR 612.2). */
function applyTextChange(current: Characteristics, effect: Effect): Characteristics {
  const pairs = strs(effect.keywords);
  if (pairs.length < 2) return current;
  const mapping = new Map<string, string>();
  for (let i = 0; i + 1 < pairs.length; i += 2) mapping.set(pairs[i] as string, pairs[i + 1] as string);
  const line = current.typeLine;
  const swapped = line.subtypes.map((s) => mapping.get(s) ?? s);
  if (swapped.every((s, i) => s === line.subtypes[i])) return current;
  const seen: string[] = [];
  for (const s of swapped) if (!seen.includes(s)) seen.push(s);
  return withTypeLine(current, { supertypes: line.supertypes, types: line.types, subtypes: seen });
}

function isLandType(subtype: string): boolean {
  return (activeRegistry().typesFor(subtype) & CardType.LAND) !== 0;
}

/** Set a type line and refresh the abilities its types imply (CR 305.6). */
function withTypeLine(current: Characteristics, line: TypeLine): Characteristics {
  const kept = current.abilities.filter((a) => !flag(a.from_land_type));
  return replaceCharacteristics(current, { typeLine: line, abilities: [...kept, ...intrinsicAbilities(line)] });
}

const INTRINSIC_CACHE = new Map<string, readonly Ability[]>();

/** Abilities an object has from its type line alone (CR 305.6, 310.12b). */
export function intrinsicAbilities(line: CardTypeLine | TypeLine): readonly Ability[] {
  const key = `${line.supertypes}|${line.types}|${line.subtypes.join(",")}`;
  let cached = INTRINSIC_CACHE.get(key);
  if (cached === undefined) {
    cached = [...intrinsicLandAbilities(line), ...intrinsicBattleAbilities(line)];
    INTRINSIC_CACHE.set(key, cached);
  }
  return cached;
}

function intrinsicLandAbilities(line: CardTypeLine | TypeLine): Ability[] {
  if ((line.types & CardType.LAND) === 0) return [];
  const out: Ability[] = [];
  for (const subtype of line.subtypes) {
    const color = BASIC_LAND_MANA.get(subtype);
    if (color === undefined) continue;
    out.push({
      $: "Ability",
      kind: ir("AbilityKind.ACTIVATED"),
      effects: [{ $: "Effect", kind: ir("EffectKind.ADD_MANA"), colors: colorLiteral(color), text: `Add ${subtype} mana` }],
      cost: { $: "Cost", components: [{ $: "CostComponent", kind: ir("CostKind.TAP_SELF"), text: "{T}" }] },
      is_mana_ability: true,
      text: `{T}: Add mana (${subtype})`,
      from_land_type: true,
    });
  }
  return out;
}

const COLOR_NAMES: ReadonlyMap<Color, string> = new Map([
  [Color.WHITE, "WHITE"],
  [Color.BLUE, "BLUE"],
  [Color.BLACK, "BLACK"],
  [Color.RED, "RED"],
  [Color.GREEN, "GREEN"],
]);

function colorLiteral(color: Color): never {
  return ir(`Color.${COLOR_NAMES.get(color) ?? "NONE"}`);
}

/** CR 310.12b: every Siege's exile-and-cast-transformed trigger. */
function intrinsicBattleAbilities(line: CardTypeLine | TypeLine): Ability[] {
  if ((line.types & CardType.BATTLE) === 0 || !line.subtypes.includes("Siege")) return [];
  return [
    {
      $: "Ability",
      kind: ir("AbilityKind.TRIGGERED"),
      trigger: {
        $: "TriggerCondition",
        event_kinds: { $set: [ir("EventKind.COUNTER_REMOVED")] },
        subject: { $: "ObjectFilter", source_only: true },
        counter_kind: "defense",
        intervening_if: {
          $: "Condition",
          kind: ir("ConditionKind.COUNTER_COUNT"),
          counter_type: "defense",
          constraint: { $: "NumericConstraint", comparison: ir("Comparison.LE"), value: { $: "Value", constant: 0 } },
          text: "no defense counters remain",
        },
        text: "when the last defense counter is removed from this permanent",
      },
      effects: [
        { $: "Effect", kind: ir("EffectKind.EXILE"), targets: { $: "ObjectFilter", source_only: true }, text: "exile it" },
        {
          $: "Effect",
          kind: ir("EffectKind.CAST_WITHOUT_PAYING"),
          targets: { $: "ObjectFilter", remembered: true },
          face_index: 1,
          text: "you may cast it transformed without paying its mana cost",
        },
      ],
      text: "When the last defense counter is removed from this permanent, exile it, then you may cast it transformed without paying its mana cost.",
    },
  ];
}

/**
 * A value in a continuous effect's context. Whose "it" is read - the object
 * being modified or the ability's source - follows `of_affected` and what
 * the value reads.
 */
function valueOf(game: Game, value: Value | undefined, ce: ContinuousEffect, obj: GameObject | null): number {
  if (value === undefined) return 0;
  // A plain constant needs no hooks (the commonest case).
  if (isConstant(value)) return value.constant ?? 0;
  const hooks = matchHooks();
  const [readsAffected, readsSource] = hooks.valueSubjects(value);
  const affected = flag(value.of_affected) || (readsAffected && !readsSource);
  const subject = affected && obj !== null ? obj.id : ce.source;
  return hooks.evaluate(game, value, { source: subject, controller: ce.controller });
}

/**
 * Layer 2 (CR 613.1b): control, recomputed from the base controller every
 * time so an expired effect hands the permanent back.
 */
function applyControl(game: Game, state: Board, byId: Map<ObjectId, GameObject>): void {
  const changes = new Map<ObjectId, [number, PlayerId]>();
  for (const ce of game.continuousEffects) {
    if (ce.expired || kindOf(ce.effect as Effect) !== EK.GAIN_CONTROL) continue;
    for (const [id, obj] of byId) {
      if (!affectsControl(game, ce, obj, state.get(id) as Characteristics)) continue;
      const existing = changes.get(id);
      if (existing === undefined || ce.timestamp > existing[0]) changes.set(id, [ce.timestamp, ce.controller]);
    }
  }
  for (const [id, obj] of byId) {
    const change = changes.get(id);
    let controller: PlayerId;
    if (change !== undefined) controller = change[1];
    else if (obj.zone === Zone.BATTLEFIELD) controller = obj.baseController;
    else continue;
    if (controller === NO_PLAYER || obj.controller === controller) continue;
    obj.controller = controller;
    // CR 302.6: a change of control restarts summoning sickness; CR 702.30a.
    obj.summoningSick = true;
    obj.newlyControlled = true;
  }
}

function affectsControl(game: Game, ce: ContinuousEffect, obj: GameObject, chars: Characteristics): boolean {
  const spec = (ce.effect as Effect).targets;
  if (spec === undefined || spec === null) return obj.id === ce.source;
  return matchHooks().matches(game, obj, spec, { source: ce.source, controller: ce.controller, override: chars });
}

const ENCHANT_CREATURE: Ability = {
  $: "Ability",
  kind: ir("AbilityKind.STATIC"),
  keyword: "Enchant",
  quality: { $: "ObjectFilter", types_all: ir("CardType.CREATURE") },
  text: "Enchant creature",
};

/** CR 702.103b: bestowed, it is an Aura with enchant creature, not a creature. */
function applyBestowed(state: Board, byId: Map<ObjectId, GameObject>): void {
  for (const [id, obj] of byId) {
    if (!obj.bestowed || (obj.zone !== Zone.STACK && obj.zone !== Zone.BATTLEFIELD)) continue;
    const chars = state.get(id) as Characteristics;
    state.set(id, replaceCharacteristics(chars, {
      typeLine: { supertypes: chars.typeLine.supertypes, types: CardType.ENCHANTMENT, subtypes: ["Aura"] },
      abilities: [...chars.abilities, ENCHANT_CREATURE],
    }));
  }
}

const PT_COUNTER = /^([+-]\d+)\/([+-]\d+)$/;

export const KEYWORD_COUNTERS: ReadonlySet<string> = new Set([
  "flying", "first strike", "double strike", "deathtouch", "decayed",
  "exalted", "haste", "hexproof", "indestructible", "lifelink",
  "menace", "reach", "shadow", "trample", "vigilance",
]);

/** Layer 6: a keyword counter grants its keyword (CR 613.1f, 122.1b). */
function applyKeywordCounters(obj: GameObject, current: Characteristics): Characteristics {
  if (obj.counters.size === 0) return current;
  const granted = [...obj.counters.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .filter(([name, count]) => count > 0 && KEYWORD_COUNTERS.has(name.toLowerCase()))
    .map(([name]) => keywordAbility(name));
  return granted.length === 0 ? current : replaceCharacteristics(current, { abilities: [...current.abilities, ...granted] });
}

/** Layer 7d: every "+X/+Y" counter (CR 613.4c, 122.1a). */
function applyCounters(obj: GameObject, current: Characteristics): Characteristics {
  if (obj.counters.size === 0) return current;
  let power = 0;
  let toughness = 0;
  for (const [name, count] of obj.counters) {
    if (count <= 0) continue;
    const m = PT_COUNTER.exec(name);
    if (m) {
      power += Number.parseInt(m[1] as string, 10) * count;
      toughness += Number.parseInt(m[2] as string, 10) * count;
    }
  }
  if (power === 0 && toughness === 0) return current;
  return replaceCharacteristics(current, { power: (current.power ?? 0) + power, toughness: (current.toughness ?? 0) + toughness });
}

/**
 * Layer 7a: characteristic-defining abilities (CR 613.4a, 604.3). A "*" no
 * CDA defines settles at zero on the battlefield.
 */
function applyCda(game: Game, obj: GameObject, current: Characteristics): Characteristics {
  let chars = current;
  for (const ability of current.abilities) {
    if (!flag(ability.is_characteristic_defining)) continue;
    for (const effect of list(ability.effects)) {
      if (kindOf(effect) !== EK.SET_PT) continue;
      const evaluate = (v: Value | undefined): number =>
        v === undefined ? 0 : isConstant(v) ? (v.constant ?? 0) : matchHooks().evaluate(game, v, { source: obj.id, controller: obj.controller });
      chars = replaceCharacteristics(chars, {
        power: isUnchanged(effect.amount) ? chars.power : evaluate(effect.amount),
        toughness: isUnchanged(effect.amount2) ? chars.toughness : evaluate(effect.amount2),
      });
    }
  }
  if (obj.zone === Zone.BATTLEFIELD && isCreature(chars) && (chars.power === null || chars.toughness === null)) {
    chars = replaceCharacteristics(chars, { power: chars.power ?? 0, toughness: chars.toughness ?? 0 });
  }
  return chars;
}
