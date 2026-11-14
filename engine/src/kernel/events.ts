/**
 * Game events: what triggers watch (CR 603.2) and replacement effects
 * intercept (CR 614.1). Ported from mtgfish/rules/kernel/events.py.
 *
 * One flat record with a `kind` discriminator, as in Python. EventKind values
 * are generated from the Python enum so the card IR's event kinds match.
 */

import { zoneName, Zone } from "./enums";
import { NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "./ids";

export const EventKind = {
  ZONE_CHANGE: 0,
  ENTERS_BATTLEFIELD: 1,
  LEAVES_BATTLEFIELD: 2,
  DIES: 3,
  PUT_INTO_GRAVEYARD: 4,
  EXILED: 5,
  RETURNED_TO_HAND: 6,
  SHUFFLED_INTO_LIBRARY: 7,
  TOKEN_CREATED: 8,
  CEASED_TO_EXIST: 9,
  CAST_SPELL: 20,
  SPELL_RESOLVED: 21,
  ABILITY_ACTIVATED: 22,
  ABILITY_TRIGGERED: 23,
  ABILITY_RESOLVED: 24,
  COUNTERED: 25,
  FIZZLED: 26,
  TARGETED: 27,
  COPIED: 28,
  PUT_ON_STACK: 29,
  TAPPED: 40,
  UNTAPPED: 41,
  COUNTER_ADDED: 42,
  COUNTER_REMOVED: 43,
  ATTACHED: 44,
  UNATTACHED: 45,
  TRANSFORMED: 46,
  TURNED_FACE_UP: 47,
  TURNED_FACE_DOWN: 48,
  PHASED_OUT: 49,
  PHASED_IN: 50,
  CONTROL_CHANGED: 51,
  SACRIFICED: 52,
  DESTROYED: 53,
  REGENERATED: 54,
  DAMAGE_DEALT: 60,
  COMBAT_DAMAGE_DEALT: 61,
  LIFE_GAINED: 62,
  LIFE_LOST: 63,
  LIFE_SET: 64,
  DAMAGE_PREVENTED: 65,
  POISON_ADDED: 66,
  SPEED_INCREASED: 67,
  DREW_CARD: 80,
  DISCARDED: 81,
  MILLED: 82,
  REVEALED: 83,
  SCRIED: 84,
  SURVEILLED: 85,
  SEARCHED_LIBRARY: 86,
  CLASS_LEVEL_GAINED: 87,
  SHUFFLED: 88,
  ATTACKERS_DECLARED: 100,
  ATTACKS: 101,
  BLOCKERS_DECLARED: 102,
  BLOCKS: 103,
  BECOMES_BLOCKED: 104,
  BECOMES_BLOCKED_BY: 18001,
  ATTACKS_UNBLOCKED: 18003,
  REMOVED_FROM_COMBAT: 105,
  TURN_BEGAN: 120,
  PHASE_BEGAN: 121,
  STEP_BEGAN: 122,
  STEP_ENDED: 123,
  PHASE_ENDED: 124,
  TURN_ENDED: 125,
  UNTAP_STEP: 126,
  UPKEEP: 127,
  DRAW_STEP: 128,
  END_STEP: 129,
  CLEANUP: 130,
  PLAYER_LOST: 140,
  PLAYER_WON: 141,
  PLAYER_LEFT_GAME: 142,
  MANA_ADDED: 143,
  MANA_SPENT: 144,
  MANA_EMPTIED: 145,
  PRIORITY_RECEIVED: 146,
  LAND_PLAYED: 147,
  TAPPED_FOR_MANA: 148,
  COMMANDER_CAST: 160,
  COMMANDER_MOVED_TO_COMMAND_ZONE: 161,
  COMMANDER_DAMAGE_DEALT: 162,
  BECAME_MONARCH: 180,
  TOOK_INITIATIVE: 181,
  ENERGY_GAINED: 182,
  EXPERIENCE_GAINED: 183,
  DAY_NIGHT_CHANGED: 184,
  RING_TEMPTED: 185,
  EXPLORED: 187,
  DUNGEON_VENTURED: 186,
  DESCENDED: 330,
  CRIME_COMMITTED: 331,
  EXPENDED: 332,
  BECAME_SADDLED: 380,
  GIFT_GIVEN: 370,
  VENTURE_MARKER_MOVED: 300,
  DUNGEON_COMPLETED: 301,
  MANIFESTED_DREAD: 310,
  ATTRACTION_OPENED: 350,
  ROLLED_TO_VISIT: 351,
  ATTRACTION_VISITED: 352,
  BECAME_MONSTROUS: 19001,
  BECAME_RENOWNED: 18002,
} as const;
export type EventKind = number;

const EVENT_KIND_NAMES: ReadonlyMap<number, string> = new Map(
  Object.entries(EventKind).map(([k, v]) => [v, k] as [number, string]),
);

export function eventKindName(kind: EventKind): string {
  return EVENT_KIND_NAMES.get(kind) ?? String(kind);
}

/** A permanent left the battlefield in some form (CR 603.6e, 608.2g). */
export const LEAVE_BATTLEFIELD_KINDS: ReadonlySet<EventKind> = new Set([EventKind.LEAVES_BATTLEFIELD, EventKind.DIES, EventKind.SACRIFICED, EventKind.DESTROYED]);

/** CR 603.10: events whose triggers look back in time. */
export const LOOK_BACK_KINDS: ReadonlySet<EventKind> = new Set([...LEAVE_BATTLEFIELD_KINDS, EventKind.UNATTACHED, EventKind.PHASED_OUT, EventKind.CONTROL_CHANGED]);

/**
 * One thing that happened, or is about to. Replacement effects see it before
 * it applies and may return a modified copy (CR 614.1); everything else sees
 * it after. Treated as immutable: edit with `replaced`.
 */
export interface GameEvent {
  readonly kind: EventKind;
  /** The object it is about - the permanent that died, the spell cast. */
  readonly objectId: ObjectId;
  /** The player it is about - who drew, who lost life. */
  readonly player: PlayerId;
  /** What caused it; for damage, the source (deathtouch, lifelink, commander damage). */
  readonly source: ObjectId;
  /** The source's controller at the time, captured before it can change. */
  readonly sourceController: PlayerId;
  readonly amount: number;
  readonly fromZone: Zone | null;
  readonly toZone: Zone | null;
  /** Kind-specific extras, e.g. the counter type for COUNTER_ADDED. */
  readonly data: readonly unknown[];
}

export function event(kind: EventKind, fields: Partial<Omit<GameEvent, "kind">> = {}): GameEvent {
  return {
    kind,
    objectId: NO_OBJECT,
    player: NO_PLAYER,
    source: NO_OBJECT,
    sourceController: NO_PLAYER,
    amount: 0,
    fromZone: null,
    toZone: null,
    data: [],
    ...fields,
  };
}

/** A modified copy - how a replacement effect edits an event. */
export function replaced(e: GameEvent, changes: Partial<Omit<GameEvent, "kind">>): GameEvent {
  return { ...e, ...changes };
}

export function withAmount(e: GameEvent, amount: number): GameEvent {
  return { ...e, amount };
}

export function isLeaveBattlefield(e: GameEvent): boolean {
  return LEAVE_BATTLEFIELD_KINDS.has(e.kind);
}

/**
 * CR 603.10: whether this event's triggers see the game as it was before it.
 * A card leaving a graveyard looks back too (CR 603.10a).
 */
export function looksBackInTime(e: GameEvent): boolean {
  if (e.kind === EventKind.ZONE_CHANGE) return e.fromZone === Zone.GRAVEYARD;
  return LOOK_BACK_KINDS.has(e.kind);
}

export function eventText(e: GameEvent): string {
  const parts = [eventKindName(e.kind)];
  if (e.objectId !== NO_OBJECT) parts.push(`obj=${e.objectId}`);
  if (e.player !== NO_PLAYER) parts.push(`player=${e.player}`);
  if (e.source !== NO_OBJECT) parts.push(`src=${e.source}`);
  if (e.amount) parts.push(`amount=${e.amount}`);
  if (e.fromZone !== null || e.toZone !== null) {
    const from = e.fromZone !== null ? zoneName(e.fromZone) : "-";
    const to = e.toZone !== null ? zoneName(e.toZone) : "-";
    parts.push(`${from}->${to}`);
  }
  if (e.data.length > 0) parts.push(`data=(${e.data.map(String).join(", ")})`);
  return parts.join(" ");
}

