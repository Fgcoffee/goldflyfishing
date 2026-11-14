/**
 * MTG Arena's digital-only mechanics: perpetually, conjure and seek. Ported
 * from mtgfish/rules/cr700_additional_rules/digital_mechanics.py.
 *
 * None is in the Comprehensive Rules; their rules are what Arena publishes.
 * The CR still governs everything around them.
 *
 * - **Seek:** "put one at random from your library into your hand". Not a
 *   search (no shuffle, nothing revealed) and not a draw.
 * - **Conjure:** a card created from outside the game in the named zone,
 *   owned by the player who conjures it, a normal card from then on. The
 *   card is found by name through the game's card catalog.
 * - **Perpetually:** a change that applies to the card for the rest of the
 *   game, whatever zone it moves to - the exception to CR 400.7. Recorded
 *   against the card, carried to the new object at every zone change, and
 *   handed to the layer system as a continuous effect with its creation
 *   timestamp (CR 613.7). Numbers in it are fixed as it is created.
 *
 * Python keeps `Game.perpetual` and `Game.card_catalog`. The TS Game has
 * neither field yet, so both live as properties set on the game object by
 * this module: the perpetual record is replaced, never mutated, so a
 * `cloneGame` (which spreads the game) shares it safely, and the catalog is
 * card data, shared on purpose.
 */

import type { Effect, ObjectFilter } from "../../ir";
import type { CardDef } from "../../kernel/card";
import { Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { invalidateAllCharacteristics, logRecord, player, zoneList, type ContinuousEffect, type Game } from "../../kernel/game";
import { isLive, newGameObject, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { allocateObjectId, allocateTimestamp, NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { randrange } from "../../kernel/random";
import { emit } from "../emit";
import { ir, num, strs } from "../ir_read";
import { addCounters } from "../cr100_game_concepts/actions";
import { isPermanentType } from "../cr200_parts_of_a_card/typeline";
import { characteristicsOf, layerFor } from "../cr600_spells_and_abilities/layers";
import { replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";
import { chooseProtector, enteringCounters, moveObject } from "../zones";

/** What the game's card catalog must offer: a card definition by name. */
export interface CardCatalog {
  lookup(name: string): unknown | null;
}

/** One perpetual change bound to one card, aimed at the card itself, with constant amounts. */
export interface PerpetualChange {
  readonly effect: Effect;
  readonly controller: PlayerId;
  readonly timestamp: number;
}

interface DigitalGame {
  perpetual?: ReadonlyMap<ObjectId, readonly PerpetualChange[]>;
  cardCatalog?: CardCatalog | null;
}
const digital = (game: Game) => game as Game & DigitalGame;

/** The record of perpetual changes (Python `Game.perpetual`). Read-only: writers replace it. */
export function perpetualRecord(game: Game): ReadonlyMap<ObjectId, readonly PerpetualChange[]> {
  return digital(game).perpetual ?? new Map();
}

function setPerpetual(game: Game, id: ObjectId, changes: readonly PerpetualChange[]): void {
  const next = new Map(perpetualRecord(game));
  next.set(id, changes);
  digital(game).perpetual = next;
}

/**
 * CR 400.7's exception, for zones.ts: carry a card's perpetual changes to the
 * new object a zone change makes (Python `Game.move_object`).
 */
export function carryPerpetual(game: Game, from: ObjectId, to: ObjectId): void {
  const changes = perpetualRecord(game).get(from);
  if (changes !== undefined && changes.length) setPerpetual(game, to, changes);
}

/** The game's card catalog, for conjure (Python `Game.card_catalog`). */
export function setCardCatalog(game: Game, catalog: CardCatalog | null): void {
  digital(game).cardCatalog = catalog;
}

// ---------------------------------------------------------------------------
// Perpetually
// ---------------------------------------------------------------------------

/** The changes "perpetually" can make: the layered continuous effects. */
export const PERPETUAL_KINDS: ReadonlySet<number> = new Set(
  ["MODIFY_PT", "SET_PT", "SWITCH_PT", "GRANT_ABILITY", "REMOVE_ABILITIES", "SET_COLOR", "ADD_TYPE", "REMOVE_TYPE", "SET_TYPE"].map(
    (n) => ir(`EffectKind.${n}` as never) as number,
  ),
);
const PERMANENT: number = ir("Duration.PERMANENT");

/**
 * Bind a change to a card for the rest of the game. `amounts` are the
 * change's two numbers, worked out now: they are fixed as it is created.
 */
export function perpetually(
  game: Game,
  obj: GameObject,
  effect: Effect,
  controller: PlayerId,
  options: { amounts?: readonly [number, number] | null } = {},
): PerpetualChange | null {
  if (!PERPETUAL_KINDS.has(num(effect.kind)) || !isLive(obj)) return null;
  const bound: Effect = { ...effect, targets: null, is_targeted: false, duration: PERMANENT };
  if (options.amounts != null) {
    bound.amount = { $: "Value", constant: options.amounts[0] };
    bound.amount2 = { $: "Value", constant: options.amounts[1] };
  }
  const change: PerpetualChange = { effect: bound, controller, timestamp: allocateTimestamp(game.ids) };
  setPerpetual(game, obj.id, [...(perpetualRecord(game).get(obj.id) ?? []), change]);
  invalidateAllCharacteristics(game);
  return change;
}

export function perpetualChanges(game: Game, obj: GameObject): readonly PerpetualChange[] {
  return perpetualRecord(game).get(obj.id) ?? [];
}

/** Every live card carrying a perpetual change, wherever it is - for the layer system. */
export function perpetuallyChanged(game: Game): GameObject[] {
  const out: GameObject[] = [];
  for (const id of perpetualRecord(game).keys()) {
    const obj = game.objects.get(id);
    if (obj !== undefined && isLive(obj)) out.push(obj);
  }
  return out;
}

/**
 * The perpetual changes of the objects being computed, as layer inputs:
 * each a continuous effect whose source is the card it changes.
 */
export function perpetualContinuousEffects(game: Game, byId: ReadonlyMap<ObjectId, GameObject>): ContinuousEffect[] {
  const out: ContinuousEffect[] = [];
  const record = perpetualRecord(game);
  for (const [id, obj] of byId) {
    for (const change of record.get(id) ?? []) {
      out.push({
        effect: change.effect,
        source: obj.id,
        controller: change.controller,
        timestamp: change.timestamp,
        layer: layerFor(change.effect),
        duration: PERMANENT,
        createdTurn: 0,
        ability: null,
        fromStaticAbility: false,
        isCda: false,
        expired: false,
      });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Conjure
// ---------------------------------------------------------------------------

/**
 * Create a card from outside the game in `zone`, owned by the player. It
 * arrives as any card does: replacements first (CR 614), a permanent applies
 * its "as this enters" effects and gets its loyalty or defense (CR 306.5b,
 * 310.4b), and the zone change is announced with no zone of origin.
 * `fromTop` places it that far down a library; zero, at the bottom.
 */
export function conjure(
  game: Game,
  playerId: PlayerId,
  card: unknown,
  zone: Zone,
  options: { tapped?: boolean; fromTop?: number; source?: ObjectId } = {},
): GameObject | null {
  const source = options.source ?? NO_OBJECT;
  const p = player(game, playerId);
  if (p.hasLost) return null;
  const obj = newGameObject(allocateObjectId(game.ids), {
    kind: ObjectKind.CARD,
    owner: playerId,
    controller: playerId,
    baseController: playerId,
    zone,
    card,
    timestamp: allocateTimestamp(game.ids),
  });
  game.objects.set(obj.id, obj);

  const hooks = replacementHooks();
  let prospective = event(EventKind.ZONE_CHANGE, { objectId: obj.id, player: playerId, source, toZone: zone });
  if (hooks.hasReplacements(game)) {
    const result = hooks.replace(game, prospective);
    if (result === null) {
      game.objects.delete(obj.id);
      return null;
    }
    prospective = result;
  }
  const to = prospective.toZone ?? zone;
  obj.zone = to;

  let entering: [string, number][] = [];
  if (to === Zone.BATTLEFIELD) {
    // CR 304.4, 307.4: a card with nowhere to be is not created.
    if (!mayEnterTheBattlefield(game, obj)) {
      game.objects.delete(obj.id);
      return null;
    }
    entering = enteringCounters(characteristicsOf(game, obj)); // read before (CR 306.5c)
    obj.enteredBattlefieldTurn = game.turn;
    obj.summoningSick = true;
    obj.tapped = options.tapped ?? false;
    chooseProtector(game, obj);
  }
  const target = zoneList(game, to, playerId);
  const fromTop = options.fromTop ?? 0;
  if (to === Zone.LIBRARY && fromTop > 0) target.splice(Math.min(fromTop - 1, target.length), 0, obj.id);
  else target.push(obj.id);
  invalidateAllCharacteristics(game);
  // CR 122.6, 614.1c: entering with counters is having them put on it.
  for (const [kind, amount] of entering) addCounters(game, obj, kind, amount, { source: obj.id });
  const name = (card as { name?: string } | null)?.name ?? "a card";
  logRecord(game, `${p.name} conjures ${name}`, { kind: "conjure", player: playerId });
  emit(game, event(EventKind.ZONE_CHANGE, { objectId: obj.id, player: playerId, source, toZone: to }));
  // Python's _emit_zone_specific(obj, None, zone).
  if (to === Zone.BATTLEFIELD) {
    hooks.applySelfEntryReplacements(game, obj);
    emit(game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: obj.id, player: obj.controller, fromZone: null }));
  } else if (to === Zone.GRAVEYARD) {
    emit(game, event(EventKind.PUT_INTO_GRAVEYARD, { objectId: obj.id, player: obj.owner, fromZone: null, toZone: to }));
  }
  return obj;
}

/** CR 304.4, 307.4: only a card with a permanent face becomes a permanent (as zones.ts). */
function mayEnterTheBattlefield(game: Game, obj: GameObject): boolean {
  if (isPermanentType(characteristicsOf(game, obj).typeLine)) return true;
  return ((obj.card as Partial<CardDef> | null)?.faces ?? []).some((f) => isPermanentType(f.type_line));
}

/**
 * Conjure the same card as `original`. A duplicate keeps its perpetual
 * changes; a card merely named the same doesn't.
 */
export function conjureDuplicate(
  game: Game,
  playerId: PlayerId,
  original: GameObject,
  zone: Zone,
  options: { keepPerpetual: boolean; tapped?: boolean; source?: ObjectId },
): GameObject | null {
  if (original.card == null || original.kind !== ObjectKind.CARD) return null; // a token has no card
  const made = conjure(game, playerId, original.card, zone, { tapped: options.tapped ?? false, source: options.source ?? NO_OBJECT });
  if (made !== null && options.keepPerpetual) {
    const carried = perpetualRecord(game).get(original.id);
    if (carried !== undefined && carried.length) {
      setPerpetual(game, made.id, carried);
      invalidateAllCharacteristics(game);
    }
  }
  return made;
}

/** How a CONJURE effect carries the printed name it conjures: a `keywords` entry with this prefix. */
export const NAMED = "named:";

export function named(name: string): string {
  return NAMED + name;
}

/** The card name a CONJURE effect prints, or "". */
export function conjuredName(effect: Effect): string {
  for (const word of strs(effect.keywords)) if (word.startsWith(NAMED)) return word.slice(NAMED.length);
  return "";
}

/** The card a conjure names, or null - logged - if it can't be found. */
export function catalogCard(game: Game, name: string): unknown | null {
  const catalog = digital(game).cardCatalog ?? null;
  const card = catalog !== null ? catalog.lookup(name) : null;
  if (card == null) {
    const why = catalog === null ? "no card catalogue for this game" : "no such card";
    logRecord(game, `cannot conjure ${JSON.stringify(name)}: ${why}`, { kind: "unsupported" });
    return null;
  }
  return card;
}

// ---------------------------------------------------------------------------
// Seek
// ---------------------------------------------------------------------------

/**
 * Put `count` cards at random from those in the player's library matching
 * `wanted` (null: any card) into `toZone` (their hand); fewer if there are
 * fewer. The pick draws from the game's PRNG, so a replay picks the same.
 */
export function seek(
  game: Game,
  playerId: PlayerId,
  wanted: ObjectFilter | null,
  count: number,
  options: { source?: ObjectId; controller?: PlayerId | null; toZone?: Zone } = {},
): GameObject[] {
  const source = options.source ?? NO_OBJECT;
  const asker = options.controller ?? playerId;
  const pool: GameObject[] = [];
  for (const id of player(game, playerId).library) {
    const obj = game.objects.get(id);
    if (obj === undefined) continue;
    if (wanted === null || matchHooks().matches(game, obj, wanted, { source, controller: asker })) pool.push(obj);
  }
  const picked: GameObject[] = [];
  for (let i = 0; i < Math.max(0, count) && pool.length; i++) {
    const [obj] = pool.splice(randrange(game.rng, pool.length), 1);
    picked.push(moveObject(game, obj!, options.toZone ?? Zone.HAND, { toPlayer: playerId }));
  }
  return picked;
}
