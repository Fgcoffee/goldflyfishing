/**
 * Evaluating ObjectFilters and PlayerFilters against the game. Ported from
 * mtgfish/rules/kernel/matching.py.
 *
 * Every unsupported constraint fails closed: if a filter asks something this
 * module cannot answer, the object does not match. An over-broad match lets
 * a spell hit what it should not; a missed one merely does less.
 *
 * IR objects arrive as JSON with their defaults left out, so every field is
 * read with its default (`num`, `numSet`, `str`, `list` from ../ir_read).
 */

import type { NumericConstraint, ObjectFilter, PlayerFilter, Value } from "../../ir";
import { CardType, Color, Zone } from "../../kernel/enums";
import { apnapOrder, player as playerOf, zoneList, type Game } from "../../kernel/game";
import { counterCount, enteredThisTurn, isLive, isToken, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";
import {
  basePower,
  baseToughness,
  charsManaValue,
  hasKeyword,
  hasType,
  type Characteristics,
} from "../cr200_parts_of_a_card/characteristics";
import { characteristicsOf } from "../cr600_spells_and_abilities/layers";
import { list, num, numSet, str, strs } from "../ir_read";
import { ControllerRelation, PlayerScope, ValueKind } from "./enums";
import { evaluate } from "./values";

/** Who is asking: the ability's source and its controller ("you"). */
export interface MatchOptions {
  readonly source?: ObjectId;
  readonly controller?: PlayerId;
  /** In-progress characteristics from the layer system (CR 613.8a). */
  readonly override?: Characteristics;
  /** Allow a superseded object - last-known information only (CR 603.6e). */
  readonly allowStale?: boolean;
}

/**
 * The combat state matching reads (Python's Combat). Until the rules session
 * ports combat, `game.combat` is read through this shape; anything else is
 * "no combat".
 */
export interface CombatView {
  readonly attacking: ReadonlyMap<ObjectId, PlayerId>;
  readonly attackingPermanent?: ReadonlyMap<ObjectId, ObjectId>;
  readonly blockers?: ReadonlyMap<ObjectId, readonly ObjectId[]>;
  readonly blocking: ReadonlyMap<ObjectId, readonly ObjectId[]>;
  readonly wasBlocked: ReadonlySet<ObjectId>;
  readonly blockersDeclared: boolean;
}

export function combatOf(game: Game): CombatView | null {
  const combat = game.combat as Partial<CombatView> | null | undefined;
  if (!combat || !(combat.attacking instanceof Map)) return null;
  return combat as CombatView;
}

/** CR 306.5c: on the battlefield a planeswalker's loyalty is its loyalty counters. */
export function currentLoyalty(obj: GameObject, chars: Characteristics): number | null {
  if (obj.zone !== Zone.BATTLEFIELD) return chars.loyalty;
  if (!hasType(chars, CardType.PLANESWALKER)) return null;
  return counterCount(obj, "loyalty");
}

function popcount(value: number): number {
  let count = 0;
  for (let v = value >>> 0; v; v &= v - 1) count++;
  return count;
}

/** Whether `obj` satisfies `spec`. */
export function matches(game: Game, obj: GameObject, spec: ObjectFilter, options: MatchOptions = {}): boolean {
  const source = options.source ?? NO_OBJECT;
  const controller = options.controller ?? NO_PLAYER;
  if (!isLive(obj) && !options.allowStale) return false;

  if (spec.source_only && !isSameObject(game, obj.id, source)) return false;
  if (spec.other_than_source && isSameObject(game, obj.id, source)) return false;
  const specific = list(spec.specific);
  if (specific.length > 0 && !specific.includes(obj.id)) return false;
  if (spec.linked_to_source && !linked(game, obj, spec, source)) return false;
  // A disjunction across fields ("artifact or Goblin spell"): one alternative
  // must match, as well as everything below. Each is asked without zones.
  const anyOf = list(spec.any_of);
  if (
    anyOf.length > 0 &&
    !anyOf.some((alt) => matches(game, obj, { ...alt, zones: { $set: [] } }, options))
  ) {
    return false;
  }

  // "This" names one object wherever it is: a source-only filter's zones are
  // the parser's default, not a claim about where it must be.
  const zones = numSet(spec.zones, [Zone.BATTLEFIELD]);
  if (zones.size > 0 && !spec.source_only && !zones.has(obj.zone)) return false;

  const fromTop = spec.from_top ?? 0;
  if (fromTop && !nearTopOfLibrary(game, obj, Number(fromTop))) return false;

  // Phased-out permanents don't exist (CR 702.26a) unless asked for.
  if (obj.phasedOut && spec.phased_out !== true) return false;

  if (!controllerMatches(game, obj, spec, controller, source)) return false;
  if (!ownerMatches(obj, spec, controller)) return false;

  const chars = options.override ?? characteristicsOf(game, obj);
  const types = chars.typeLine.types;
  const typesAll = num(spec.types_all);
  if (typesAll && (types & typesAll) !== typesAll) return false;
  const typesAny = num(spec.types_any);
  if (typesAny && !(types & typesAny)) return false;
  const typesNone = num(spec.types_none);
  if (typesNone && types & typesNone) return false;

  const supertypesAll = num(spec.supertypes_all);
  const supertypesNone = num(spec.supertypes_none);
  if (supertypesAll || supertypesNone) {
    const supertypes = chars.typeLine.supertypes;
    if (supertypesAll && (supertypes & supertypesAll) !== supertypesAll) return false;
    if (supertypesNone && supertypes & supertypesNone) return false;
  }

  const subtypes = chars.typeLine.subtypes;
  const subtypesAll = strs(spec.subtypes_all);
  if (subtypesAll.length > 0 && !subtypesAll.every((s) => subtypes.includes(s))) return false;
  const subtypesAny = strs(spec.subtypes_any);
  if (subtypesAny.length > 0 && !subtypesAny.some((s) => subtypes.includes(s))) return false;
  const subtypesNone = strs(spec.subtypes_none);
  if (subtypesNone.length > 0 && subtypesNone.some((s) => subtypes.includes(s))) return false;

  if (!colorsMatch(chars.colors, spec)) return false;
  if (spec.shares_color_with_source && !sharesAColor(game, chars, source)) return false;

  const named = strs(spec.named);
  if (named.length > 0 && !named.includes(chars.name)) return false;
  const notNamed = strs(spec.not_named);
  if (notNamed.length > 0 && notNamed.includes(chars.name)) return false;

  if (spec.tapped !== undefined && spec.tapped !== null && obj.tapped !== spec.tapped) return false;
  if (spec.face_down !== undefined && spec.face_down !== null && obj.faceDown !== spec.face_down) return false;
  const inset = str(spec.has_inset);
  if (inset && !hasInset(game, obj, inset)) return false;
  if (spec.is_token !== undefined && spec.is_token !== null && isToken(obj) !== spec.is_token) return false;
  if (spec.is_commander !== undefined && spec.is_commander !== null && obj.isCommander !== spec.is_commander) {
    return false;
  }
  if (
    spec.summoning_sick !== undefined &&
    spec.summoning_sick !== null &&
    obj.summoningSick !== spec.summoning_sick
  ) {
    return false;
  }
  if (
    spec.arrived_since_last_turn !== undefined &&
    spec.arrived_since_last_turn !== null &&
    obj.arrivedSinceLastTurn !== spec.arrived_since_last_turn
  ) {
    return false;
  }
  if (spec.entered_this_turn !== undefined && spec.entered_this_turn !== null) {
    if (enteredThisTurn(obj, game.turn) !== spec.entered_this_turn) return false;
  }

  if (spec.in_pair_with_source !== undefined && spec.in_pair_with_source !== null) {
    const anchor = game.objects.get(source);
    const partner = anchor !== undefined ? partnerOf(game, anchor) : null;
    const inPair = anchor !== undefined && partner !== null && (obj.id === anchor.id || obj.id === partner.id);
    if (inPair !== spec.in_pair_with_source) return false;
  }

  if (spec.haunted_by_source !== undefined && spec.haunted_by_source !== null) {
    // CR 702.55b
    const anchor = game.objects.get(source);
    const haunted = anchor !== undefined && anchor.haunting !== NO_OBJECT && obj.id === anchor.haunting;
    if (haunted !== spec.haunted_by_source) return false;
  }

  if (spec.modified !== undefined && spec.modified !== null && isModified(game, obj) !== spec.modified) return false;
  if (
    spec.activated_this_turn !== undefined &&
    spec.activated_this_turn !== null &&
    obj.activationsThisTurn.size > 0 !== spec.activated_this_turn
  ) {
    return false;
  }

  if (spec.ring_bearer !== undefined && spec.ring_bearer !== null) {
    // Read-only: matching runs inside the layer system.
    const asker = controller !== NO_PLAYER ? playerOf(game, controller) : null;
    const bearing =
      asker !== null && asker.ringBearer === obj.id && obj.zone === Zone.BATTLEFIELD && obj.controller === controller;
    if (bearing !== spec.ring_bearer) return false;
  }

  const asksCombat =
    (spec.attacking !== undefined && spec.attacking !== null) ||
    (spec.blocking !== undefined && spec.blocking !== null) ||
    (spec.blocked !== undefined && spec.blocked !== null);
  if (asksCombat) {
    const combat = combatOf(game);
    if (combat === null) return false;
    const attacking = combat.attacking.has(obj.id);
    if (spec.attacking !== undefined && spec.attacking !== null && attacking !== spec.attacking) return false;
    if (spec.blocking !== undefined && spec.blocking !== null) {
      const blocking = (combat.blocking.get(obj.id) ?? []).length > 0;
      if (blocking !== spec.blocking) return false;
    }
    if (spec.blocked !== undefined && spec.blocked !== null) {
      // CR 509.1h: blocked or unblocked only once blockers are declared, and only while attacking.
      if (!combat.blockersDeclared || !attacking) return false;
      if (combat.wasBlocked.has(obj.id) !== spec.blocked) return false;
    }
  }

  const numeric: [NumericConstraint | null | undefined, number | null][] = [
    [spec.power, chars.power],
    [spec.toughness, chars.toughness],
    [spec.base_power, basePower(chars)],
    [spec.base_toughness, baseToughness(chars)],
    [spec.mana_value, charsManaValue(chars)],
  ];
  for (const [constraint, actual] of numeric) {
    if (constraint && !numericHolds(game, actual, constraint, obj, source, controller)) return false;
  }
  // CR 306.5c: loyalty on the battlefield is the board's, not the printing's.
  if (spec.loyalty && !numericHolds(game, currentLoyalty(obj, chars), spec.loyalty, obj, source, controller)) {
    return false;
  }

  if (spec.of_chosen_type || spec.of_chosen_color || spec.not_of_chosen_type || spec.of_chosen_name) {
    const chooser = game.objects.get(source);
    if (chooser === undefined) return false;
    const wanted = chooser.chosenType;
    if (spec.of_chosen_type && (!wanted || !subtypes.includes(wanted))) return false;
    if (spec.not_of_chosen_type && wanted && subtypes.includes(wanted)) return false;
    if (spec.of_chosen_color && !(chars.colors & chooser.chosenColor)) return false;
    // CR 201.4: a chosen name matching nothing in the game is not a failure of the choice.
    if (spec.of_chosen_name && (!chooser.chosenName || chars.name !== chooser.chosenName)) return false;
  }

  if (spec.is_ability !== undefined && spec.is_ability !== null) {
    if ((obj.kind === ObjectKind.ABILITY) !== spec.is_ability) return false;
  }

  const counter = str(spec.has_counter);
  if (counter) {
    const count = counterCount(obj, counter);
    if (spec.counter_constraint) {
      if (!numericHolds(game, count, spec.counter_constraint, obj, source, controller)) return false;
    } else if (count <= 0) {
      return false;
    }
  }

  const hasKw = strs(spec.has_keyword);
  if (hasKw.length > 0 && !hasKw.every((k) => hasKeyword(chars, k))) return false;
  const lacksKw = strs(spec.lacks_keyword);
  if (lacksKw.length > 0 && lacksKw.some((k) => hasKeyword(chars, k))) return false;

  if (spec.attached_to) {
    if (obj.attachedTo === NO_OBJECT) return false;
    const host = game.objects.get(obj.attachedTo);
    if (host === undefined || !matches(game, host, spec.attached_to, { source, controller })) return false;
  }

  if (spec.has_attached) {
    const hasAttached = spec.has_attached;
    const any = obj.attachments.some((a) => {
      const attached = game.objects.get(a);
      return attached !== undefined && matches(game, attached, hasAttached, { source, controller });
    });
    if (!any) return false;
  }

  return true;
}

/**
 * Whether two ids are the same object across one zone change: a dies
 * trigger's source is the card in the graveyard, while "this creature" is
 * the permanent that left. One step, deliberately.
 */
export function isSameObject(game: Game, objectId: ObjectId, source: ObjectId): boolean {
  if (objectId === source) return true;
  const sourceObj = game.objects.get(source);
  if (sourceObj !== undefined && sourceObj.previousId === objectId) return true;
  const other = game.objects.get(objectId);
  return other !== undefined && other.previousId === source;
}

function sharesAColor(game: Game, chars: Characteristics, source: ObjectId): boolean {
  const obj = game.objects.get(source);
  if (obj === undefined) return false;
  return (chars.colors & characteristicsOf(game, obj).colors) !== 0;
}

function controllerMatches(
  game: Game,
  obj: GameObject,
  spec: ObjectFilter,
  controller: PlayerId,
  source: ObjectId,
): boolean {
  const relation = num(spec.controller);
  if (relation === ControllerRelation.ANY) return true;
  if (relation === ControllerRelation.YOU) return obj.controller === controller;
  if (relation === ControllerRelation.OPPONENT) return obj.controller !== controller && controller !== NO_PLAYER;
  if (relation === ControllerRelation.SPECIFIC) return obj.controller === (spec.controller_specific ?? null);
  if (relation === ControllerRelation.SAME_AS_SOURCE) return obj.controller === controller;
  if (relation === ControllerRelation.DEFENDING_PLAYER) return defendingPlayers(game, source).includes(obj.controller);
  return true;
}

/** "Defending player" (CR 506.2): who the source attacks, or the one defender there is. */
export function defendingPlayers(game: Game, source: ObjectId): PlayerId[] {
  const combat = combatOf(game);
  if (combat === null) return [];
  const attacked = combat.attacking.get(source);
  if (attacked !== undefined) return [attacked];
  const defenders = [...new Set(combat.attacking.values())];
  return defenders.length === 1 ? defenders : [];
}

/** CR 108.3: who owns it, which is not who controls it. */
function ownerMatches(obj: GameObject, spec: ObjectFilter, controller: PlayerId): boolean {
  const relation = num(spec.owner);
  if (relation === ControllerRelation.ANY) return true;
  if (relation === ControllerRelation.YOU) return obj.owner === controller;
  if (relation === ControllerRelation.OPPONENT) return obj.owner !== controller && controller !== NO_PLAYER;
  if (relation === ControllerRelation.SPECIFIC) return obj.owner === (spec.controller_specific ?? null);
  if (relation === ControllerRelation.SAME_AS_SOURCE) return obj.owner === controller;
  return true;
}

/** CR 607.2: what the other half of a linked ability refers to (Python actions.linked_objects). */
export function linkedObjects(game: Game, sourceId: ObjectId, linkId = 0): GameObject[] {
  const recorded = game.exiledWith.get(sourceId);
  if (!recorded) return [];
  const out: GameObject[] = [];
  for (const [objectId, entryLink] of recorded) {
    if (linkId && entryLink !== linkId) continue;
    const obj = game.objects.get(objectId);
    if (obj !== undefined && isLive(obj)) out.push(obj);
  }
  return out;
}

/** CR 607.2a-c: whether the source's linked ability affected `obj`, one step back for a source that just left. */
function linked(game: Game, obj: GameObject, spec: ObjectFilter, source: ObjectId): boolean {
  const sources = [source];
  const sourceObj = game.objects.get(source);
  if (sourceObj !== undefined && sourceObj.zone !== Zone.BATTLEFIELD && sourceObj.zone !== Zone.STACK) {
    const previous = game.objects.get(sourceObj.previousId);
    if (previous !== undefined && previous.zone === Zone.BATTLEFIELD) sources.push(previous.id);
  }
  const linkId = spec.link_id ?? 0;
  return sources.some((candidate) => linkedObjects(game, candidate, linkId).some((l) => l.id === obj.id));
}

function colorsMatch(colors: Color, spec: ObjectFilter): boolean {
  if (spec.must_be_colorless && colors !== Color.NONE) return false;
  if (spec.must_be_coloured && colors === Color.NONE) return false;
  if (spec.must_be_multicolored && popcount(colors) < 2) return false;
  if (spec.must_be_monocolored && popcount(colors) !== 1) return false;
  const all = num(spec.colors_all);
  if (all && (colors & all) !== all) return false;
  const any = num(spec.colors_any);
  if (any && !(colors & any)) return false;
  const none = num(spec.colors_none);
  if (none && colors & none) return false;
  return true;
}

/** CR 702.95: the creature this one is paired with, if the pair still holds. */
export function partnerOf(game: Game, obj: GameObject): GameObject | null {
  if (obj.pairedWith === NO_OBJECT) return null;
  const partner = game.objects.get(obj.pairedWith);
  if (partner === undefined || partner.pairedWith !== obj.id) return null;
  const pairable = (o: GameObject) =>
    o.zone === Zone.BATTLEFIELD &&
    isLive(o) &&
    o.controller === obj.pairController &&
    hasType(characteristicsOf(game, o), CardType.CREATURE);
  return pairable(obj) && pairable(partner) ? partner : null;
}

/** CR 700.9: a counter on it, equipped, or enchanted by an Aura its controller controls. */
export function isModified(game: Game, obj: GameObject): boolean {
  for (const amount of obj.counters.values()) if (amount > 0) return true;
  for (const id of game.battlefield) {
    const other = game.objects.get(id);
    if (other === undefined || other.attachedTo !== obj.id) continue;
    const subtypes = characteristicsOf(game, other).typeLine.subtypes;
    if (subtypes.includes("Equipment")) return true;
    if (subtypes.includes("Aura") && other.controller === obj.controller) return true;
  }
  return false;
}

// -- numeric bounds -------------------------------------------------------------

const CHARACTERISTIC_VALUES = new Set<number>([
  ValueKind.POWER,
  ValueKind.TOUGHNESS,
  ValueKind.MANA_VALUE,
  ValueKind.COUNTERS,
]);
const PER_OBJECT_VALUES = new Set<number>([ValueKind.GREATEST_AMONG, ValueKind.LEAST_AMONG, ValueKind.TOTAL_AMONG]);
const SOURCE_VALUES = new Set<number>([
  ValueKind.X,
  ValueKind.MANA_SPENT,
  ValueKind.COST_PAID_POWER,
  ValueKind.COST_PAID,
]);
const ARITHMETIC_VALUES = new Set<number>([
  ValueKind.SUM,
  ValueKind.PRODUCT,
  ValueKind.DIFFERENCE,
  ValueKind.HALF_ROUNDED_UP,
  ValueKind.HALF_ROUNDED_DOWN,
  ValueKind.MAXIMUM,
  ValueKind.MINIMUM,
]);

/** Python Comparison.holds: EQ, NE, LT, LE, GT, GE. */
export function compare(comparison: unknown, left: number, right: number): boolean {
  switch (num(comparison)) {
    case 0:
      return left === right;
    case 1:
      return left !== right;
    case 2:
      return left < right;
    case 3:
      return left <= right;
    case 4:
      return left > right;
    default:
      return left >= right;
  }
}

/**
 * Compare one of `obj`'s numbers with a bound that may vary. A characteristic
 * marked `of_affected` is the tested object's own; anything else is a fact
 * about the ability (its X, the lands you control) and is read off the
 * source. A bound needing an ability with none to ask, or mixing both, fails.
 */
function numericHolds(
  game: Game,
  actual: number | null,
  constraint: NumericConstraint,
  obj: GameObject,
  source: ObjectId,
  controller: PlayerId,
): boolean {
  if (actual === null) return false;
  const value = constraint.value;
  if (num(value.kind) === ValueKind.CONSTANT) return compare(constraint.comparison, actual, value.constant ?? 0);
  const [affected, fromSource, contextual] = valueSubjects(value);
  if (affected && fromSource) return false;
  let subject: ObjectId;
  if (affected) subject = obj.id;
  else if (contextual && source === NO_OBJECT) return false;
  else subject = source;
  const asker = controller !== NO_PLAYER ? controller : obj.controller;
  return compare(constraint.comparison, actual, evaluate(game, value, { source: subject, controller: asker }));
}

/** What a bound reads: the tested object, the ability's source, anything at all. */
export function valueSubjects(value: Value): [boolean, boolean, boolean] {
  let affected = false;
  let fromSource = false;
  let contextual = false;
  const pending: Value[] = [value];
  while (pending.length > 0) {
    const node = pending.pop() as Value;
    const kind = num(node.kind);
    if (kind === ValueKind.CONSTANT) continue;
    if (ARITHMETIC_VALUES.has(kind)) {
      pending.push(...list(node.operands));
      continue;
    }
    contextual = true;
    if (CHARACTERISTIC_VALUES.has(kind)) {
      if (node.of_affected) affected = true;
      else fromSource = true;
    } else if (SOURCE_VALUES.has(kind)) {
      fromSource = true;
    } else if (!PER_OBJECT_VALUES.has(kind)) {
      // An unrecognised kind might read anything: walk on, so it fails closed.
      pending.push(...list(node.operands));
    }
  }
  return [affected, fromSource, contextual];
}

// -- finding ---------------------------------------------------------------------

/** Every object matching `spec`, in zone order (deterministic, for replay). */
export function find(game: Game, spec: ObjectFilter, options: MatchOptions = {}): GameObject[] {
  const zones = [...numSet(spec.zones, [Zone.BATTLEFIELD])].sort((a, b) => a - b);
  const out: GameObject[] = [];
  for (const zone of zones.length > 0 ? zones : [Zone.BATTLEFIELD]) {
    for (const obj of objectsInZone(game, zone)) {
      if (matches(game, obj, spec, { source: options.source, controller: options.controller })) out.push(obj);
    }
  }
  if (spec.source_only && out.length === 0) return theSourceWhereverItIs(game, spec, options);
  return out;
}

/** "This spell", "it": the source in whatever zone, or the object it just was (CR 603.10a). */
function theSourceWhereverItIs(game: Game, spec: ObjectFilter, options: MatchOptions): GameObject[] {
  const obj = game.objects.get(options.source ?? NO_OBJECT);
  if (obj === undefined || !isLive(obj)) return [];
  const anywhere: ObjectFilter = { ...spec, zones: { $set: [] } };
  const previous = obj.previousId ? game.objects.get(obj.previousId) : undefined;
  for (const candidate of [obj, previous]) {
    if (
      candidate !== undefined &&
      matches(game, candidate, anywhere, { source: options.source, controller: options.controller, allowStale: true })
    ) {
      return [candidate];
    }
  }
  return [];
}

/** CR 401.2: "the top N cards" of the owner's library. */
function nearTopOfLibrary(game: Game, obj: GameObject, depth: number): boolean {
  if (obj.zone !== Zone.LIBRARY) return false;
  return playerOf(game, obj.owner).library.slice(0, depth).includes(obj.id);
}

function* objectsInZone(game: Game, zone: Zone): Generator<GameObject> {
  if (zone === Zone.BATTLEFIELD || zone === Zone.STACK || zone === Zone.EXILE || zone === Zone.COMMAND) {
    for (const id of zoneList(game, zone)) {
      const obj = game.objects.get(id);
      if (obj !== undefined) yield obj;
    }
    return;
  }
  for (const p of game.players) {
    for (const id of zoneList(game, zone, p.id)) {
      const obj = game.objects.get(id);
      if (obj !== undefined) yield obj;
    }
  }
}

// -- players ---------------------------------------------------------------------

/** Which players a PlayerFilter names, in APNAP order (CR 101.4). */
export function resolvePlayers(
  game: Game,
  spec: PlayerFilter,
  options: { controller?: PlayerId; source?: ObjectId } = {},
): PlayerId[] {
  const controller = options.controller ?? NO_PLAYER;
  const scope = num(spec.scope);
  if (scope === PlayerScope.SPECIFIC && spec.specific !== undefined && spec.specific !== null) return [spec.specific];
  if (scope === PlayerScope.YOU) return controller !== NO_PLAYER ? [controller] : [];
  if (scope === PlayerScope.ACTIVE_PLAYER) return [game.activePlayer];
  if (scope === PlayerScope.MONARCH) return game.players.filter((p) => p.isMonarch && !p.hasLost).map((p) => p.id);
  if (scope === PlayerScope.ENCHANTED_PLAYER) {
    // CR 303.4b: the player the source Aura is attached to.
    const aura = game.objects.get(options.source ?? NO_OBJECT);
    if (aura === undefined || aura.attachedPlayer === NO_PLAYER) return [];
    return playerOf(game, aura.attachedPlayer).hasLost ? [] : [aura.attachedPlayer];
  }
  if (scope === PlayerScope.EACH_OPPONENT || scope === PlayerScope.OPPONENT || scope === PlayerScope.TARGET_OPPONENT) {
    return apnapOrder(game).filter((p) => p !== controller);
  }
  if (scope === PlayerScope.EACH_PLAYER) return apnapOrder(game);
  if (scope === PlayerScope.CONTROLLER_OF || scope === PlayerScope.OWNER_OF) {
    const obj = game.objects.get(spec.reference ?? NO_OBJECT);
    if (obj === undefined) return [];
    return [scope === PlayerScope.CONTROLLER_OF ? obj.controller : obj.owner];
  }
  return [];
}

/**
 * CR 715.2a, 720.2a: whether the object has an Adventure or an Omen - a face
 * of its card other than the front with that subtype, followed through copy
 * effects (CR 613.2); a face-down permanent has none.
 */
export function hasInset(game: Game, obj: GameObject, subtype: string): boolean {
  const seen = new Set<ObjectId>();
  let current: GameObject | undefined = obj;
  while (current !== undefined && !seen.has(current.id)) {
    seen.add(current.id);
    if (current.faceDown) return false;
    const id = current.id;
    const copying = game.continuousEffects.filter((ce) => {
      const effect = ce.effect as { kind?: unknown } | null;
      return ce.source === id && effect !== null && num(effect.kind) === COPY_PERMANENT;
    });
    if (copying.length === 0) break;
    const latest = copying.reduce((a, b) => (b.timestamp > a.timestamp ? b : a));
    current = game.objects.get((latest.effect as { copy_source?: number }).copy_source ?? NO_OBJECT);
  }
  if (current === undefined) return false;
  const faces = (current.card as { faces?: readonly { type_line?: { subtypes?: readonly unknown[] } }[] } | null)?.faces ?? [];
  return faces.slice(1).some((face) => strs((face.type_line?.subtypes ?? []) as never).includes(subtype));
}

import { EffectKindValues } from "../../ir";
const COPY_PERMANENT = EffectKindValues.COPY_PERMANENT ?? -1;
