/**
 * Looking a few steps ahead: what an action would do, before doing it.
 * Ported from mtgfish/ai/lookahead.py.
 *
 * The action is played out on a private copy of the game - cast, resolve,
 * state-based actions, the triggers it sets off and their resolution - and
 * what changed is reported. The real game isn't touched.
 *
 * What the bot may not know stays unknown: the copy's libraries are
 * shuffled before anything is drawn, so "draw a card" shows a card drawn,
 * not which. Opponents are assumed to pass.
 *
 * The copy's randomness is its own: `cloneGame` copies the PRNG, and the
 * library shuffle uses a PRNG seeded from the game's position, so nothing
 * the lookahead does advances the real game's random state or its log.
 */

import { cloneGame, permanents, type Game } from "../kernel/game";
import type { ObjectId, PlayerId } from "../kernel/ids";
import { logDigest } from "../kernel/log";
import { newRng, shuffle } from "../kernel/random";
import { perform, settle, type Action } from "../rules/cr100_game_concepts/priority";
import { characteristicsOf } from "../rules/cr600_spells_and_abilities/layers";
import { resolveTop } from "../rules/cr600_spells_and_abilities/stack";

/** Stack objects a projection resolves before giving up: a chain is a handful; this stops a loop. */
export const MAX_RESOLUTIONS = 24;

/** How one player's resources changed. */
export interface PlayerDelta {
  life: number;
  cardsDrawn: number;
  hand: number;
  library: number;
  graveyard: number;
  poison: number;
}

const ZERO_DELTA: PlayerDelta = { life: 0, cardsDrawn: 0, hand: 0, library: 0, graveyard: 0, poison: 0 };

/** What an action would do, measured on a copy of the game. */
export interface Projection {
  /** The engine accepted the action; false means illegal as offered, and nothing else is filled in. */
  legal: boolean;
  /** Something other than paying for the action changed. */
  changed: boolean;
  players: Map<PlayerId, PlayerDelta>;
  /** Names of permanents that arrived or left, by controller. */
  entered: Map<PlayerId, string[]>;
  left: Map<PlayerId, string[]>;
  /** Stack objects resolved while projecting: the action plus every trigger it set off. */
  resolutions: number;
  /** The stack wasn't empty when the projection stopped: a loop, or a chain past MAX_RESOLUTIONS. */
  unfinished: boolean;
}

function newProjection(fields: Partial<Projection>): Projection {
  return { legal: false, changed: false, players: new Map(), entered: new Map(), left: new Map(), resolutions: 0, unfinished: false, ...fields };
}

/**
 * A fixed-length summary from `playerId`'s point of view, for a learned
 * policy: own deltas, then the sum over opponents, then the board and the
 * chain. Python `Projection.features`.
 */
export function features(p: Projection, playerId: PlayerId): number[] {
  const own = p.players.get(playerId) ?? ZERO_DELTA;
  const others = [...p.players].filter(([id]) => id !== playerId).map(([, d]) => d);
  const total = (k: keyof PlayerDelta) => others.reduce((s, d) => s + d[k], 0);
  const count = (m: Map<PlayerId, string[]>, mine: boolean) =>
    [...m].filter(([id]) => (id === playerId) === mine).reduce((s, [, v]) => s + v.length, 0);
  return [
    Number(p.legal), Number(p.changed),
    own.life, own.cardsDrawn, own.hand, own.library, own.graveyard,
    total("life"), total("cardsDrawn"), total("hand"), total("poison"),
    count(p.entered, true), count(p.left, true), count(p.entered, false), count(p.left, false),
    p.resolutions, Number(p.unfinished),
  ];
}

/** Play `act` out on a copy of the game and report what it did. */
export function project(game: Game, playerId: PlayerId, act: Action): Projection {
  const source = game.objects.get(act.source);
  const acting = source !== undefined ? characteristicsOf(game, source).name : "";
  const before = board(game, acting);
  const shadow = clone(game);
  const depth = shadow.stack.length;
  if (!perform(shadow, playerId, act)) return newProjection({ legal: false });

  let resolutions = 0;
  settle(shadow);
  // Resolve what the action put on the stack and what it triggered - not
  // what was already waiting underneath.
  while (shadow.stack.length > depth && !shadow.gameOver) {
    if (resolutions >= MAX_RESOLUTIONS) break;
    resolveTop(shadow);
    resolutions += 1;
    settle(shadow);
  }
  const after = board(shadow, acting);
  const projection = newProjection({ legal: true, resolutions, unfinished: shadow.stack.length > depth && !shadow.gameOver });
  diff(before, after, projection);
  return projection;
}

interface Cloneable {
  clone(): unknown;
}

/**
 * A private copy of the game in which the player knows only what they may:
 * `cloneGame` (card data and the ability provider shared, the PRNG copied),
 * no observer, agents copied so a decision inside the projection leaves the
 * real bots' memories alone, and every library shuffled.
 */
export function clone(game: Game): Game {
  const shadow = cloneGame(game);
  shadow.observer = null;
  shadow.agents = new Map(
    [...game.agents].map(([id, agent]) => [id, typeof (agent as Partial<Cloneable>)?.clone === "function" ? (agent as Cloneable).clone() : agent]),
  );
  hideLibraries(shadow, game);
  return shadow;
}

/** FNV-1a: a stable string hash (Python's own hash() is salted per process, so it seeded from the string). */
function fnv1a(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/**
 * Shuffle every library, so a projected draw is a card, not the card -
 * seeded from the game's position, never from its PRNG, which would advance
 * it and make the real game diverge from its replay.
 */
function hideLibraries(shadow: Game, game: Game): void {
  const rng = newRng(fnv1a(`${game.turn}|${game.step}|${logDigest(game.log)}`));
  for (const p of shadow.players) shuffle(rng, p.library);
}

// ---------------------------------------------------------------------------
// Before and after
// ---------------------------------------------------------------------------

interface Snapshot {
  /** [life, hand, library, graveyard, poison, drawn this turn] per player. */
  players: Map<PlayerId, number[]>;
  /** controller, name, power, toughness, counters, attached, types - per permanent. */
  permanents: Map<ObjectId, { controller: PlayerId; name: string; key: string }>;
  /** Every card by `${zone}|${owner}|${name}`, counted: hands, graveyards, exile. */
  cards: Map<string, number>;
  acting: string;
}

/** Everything a spell could plausibly change, and nothing about paying for it (tapped status and pools are left out). */
function board(game: Game, acting: string): Snapshot {
  const players = new Map(
    game.players.map((p) => [p.id, [p.life, p.hand.length, p.library.length, p.graveyard.length, p.poison, p.cardsDrawnThisTurn]]),
  );
  const perms = new Map<ObjectId, { controller: PlayerId; name: string; key: string }>();
  for (const obj of permanents(game)) {
    const c = characteristicsOf(game, obj);
    const counters = [...obj.counters].filter(([, v]) => v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    perms.set(obj.id, {
      controller: obj.controller,
      name: c.name,
      key: JSON.stringify([obj.controller, c.name, c.power, c.toughness, counters, obj.attachedTo, c.typeLine.types]),
    });
  }
  const cards = new Map<string, number>();
  const bump = (k: string) => cards.set(k, (cards.get(k) ?? 0) + 1);
  const nameOf = (id: ObjectId) => {
    const obj = game.objects.get(id);
    return obj !== undefined ? characteristicsOf(game, obj).name : "";
  };
  for (const p of game.players) {
    for (const id of p.hand) bump(`hand|${p.id}|${nameOf(id)}`);
    for (const id of p.graveyard) bump(`graveyard|${p.id}|${nameOf(id)}`);
  }
  for (const id of game.exile) {
    const obj = game.objects.get(id);
    if (obj !== undefined) bump(`exile|${obj.owner}|${nameOf(id)}`);
  }
  return { players, permanents: perms, cards, acting };
}

function diff(before: Snapshot, after: Snapshot, projection: Projection): void {
  for (const [id, row] of before.players) {
    const a = after.players.get(id)!;
    projection.players.set(id, {
      life: a[0]! - row[0]!,
      hand: a[1]! - row[1]!,
      library: a[2]! - row[2]!,
      graveyard: a[3]! - row[3]!,
      poison: a[4]! - row[4]!,
      cardsDrawn: a[5]! - row[5]!,
    });
  }
  const push = (m: Map<PlayerId, string[]>, id: PlayerId, name: string) => m.set(id, [...(m.get(id) ?? []), name]);
  for (const [id, row] of after.permanents) if (!before.permanents.has(id)) push(projection.entered, row.controller, row.name);
  for (const [id, row] of before.permanents) if (!after.permanents.has(id)) push(projection.left, row.controller, row.name);
  // An unfinished chain did something, even if the part seen didn't.
  projection.changed = projection.unfinished || meaningful(before, after);
}

/**
 * Whether anything changed beyond the action paying for itself: the acting
 * card leaving a hand and landing in a graveyard or exile is the spell being
 * cast and resolving. Cards are compared by name, since a returned creature
 * nets hand and graveyard sizes out.
 */
function meaningful(before: Snapshot, after: Snapshot): boolean {
  if (before.permanents.size !== after.permanents.size) return true;
  for (const [id, row] of before.permanents) if (after.permanents.get(id)?.key !== row.key) return true;
  for (const [id, row] of before.players) {
    const a = after.players.get(id)!;
    if (row[0] !== a[0] || row[2] !== a[2] || row[4] !== a[4] || row[5] !== a[5]) return true;
  }
  const subtract = (x: Map<string, number>, y: Map<string, number>) => {
    const out = new Map<string, number>();
    for (const [k, v] of x) {
      const left = v - (y.get(k) ?? 0);
      if (left > 0) out.set(k, left);
    }
    return out;
  };
  const gone = subtract(before.cards, after.cards);
  const came = subtract(after.cards, before.cards);
  for (const moved of [gone, came]) {
    for (const k of [...moved.keys()]) {
      if (k.split("|").slice(2).join("|") !== before.acting) continue;
      const left = moved.get(k)! - 1;
      if (left <= 0) moved.delete(k);
      else moved.set(k, left);
      break;
    }
  }
  return gone.size > 0 || came.size > 0;
}
