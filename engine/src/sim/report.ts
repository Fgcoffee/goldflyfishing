/**
 * Turning a run into the report: the TS port of Python
 * `mtgfish/sim/stats.py` `summarize`, for the parts a `GameRecord` carries
 * today. It answers three of the four questions in that module's docstring:
 *
 * - **Do I win?** Win rate and reason, with stall-outs counted apart.
 * - **When does my commander land?** A histogram of rounds, plus "never".
 * - **Which cards matter?** Win rate when drawn minus win rate when not,
 *   with a 95% margin, and ranked only past `MIN_SAMPLE` games each way.
 *
 * - **What do I lose to?** Removal by victim, with its sources and methods.
 *
 * Plus the per-turn series (`TurnSeries`), runaways and loop draws.
 *
 * The report is plain data (no Maps or classes), so it can cross from a
 * worker or be saved as JSON. Counters are `Record<string, number>`.
 */

import type { CrashedGame, GameRecord } from "./records";

/** A card seen fewer times than this is reported but not ranked. */
export const MIN_SAMPLE = 30;

export interface CardImpact {
  readonly name: string;
  /** Games the card was drawn in (at most once per game). */
  readonly drawn: number;
  readonly drawnWins: number;
  readonly notDrawn: number;
  readonly notDrawnWins: number;
  readonly cast: number;
  /** Mean round it was cast on, or 0 if never cast. */
  readonly averageCastRound: number;
  /** Games it was in the opening hand. */
  readonly openingHand: number;
}

/** A card of the hero's that keeps getting answered (Python `RemovalTarget`). */
export interface RemovalTarget {
  readonly victim: string;
  readonly losses: number;
  readonly bySource: Readonly<Record<string, number>>;
  readonly byMethod: Readonly<Record<string, number>>;
}

/** The source that took it most often, ties to the first seen; ["", 0] if none. */
export function worstSource(t: RemovalTarget): [string, number] {
  let best: [string, number] = ["", 0];
  for (const [name, n] of Object.entries(t.bySource)) if (n > best[1]) best = [name, n];
  return best;
}

/** The per-turn metrics a series can show (Python `METRICS`). */
export const METRICS = ["life", "lands", "manaAvailable", "cardsInHand", "permanents", "boardPower", "creatures"] as const;
export type Metric = (typeof METRICS)[number];

/** How many games behind each chart point stay clickable. */
export const EXAMPLES_PER_POINT = 20;

/**
 * One metric over time for one player, averaged over the games that reached
 * each turn (Python `TurnSeries`). `representative` is the game closest to
 * each mean: a mean may have happened in no game, the nearest one did.
 */
export interface TurnSeries {
  readonly metric: Metric;
  readonly player: number;
  readonly turns: readonly number[];
  readonly values: readonly number[];
  readonly samples: readonly number[];
  readonly examples: readonly (readonly number[])[];
  readonly representative: readonly number[];
  readonly representativeValue: readonly number[];
}

export interface Report {
  readonly games: number;
  readonly hero: number;
  readonly seats: number;
  readonly wins: number;
  readonly stalls: number;
  /** CR 104.4b: draws on a mandatory loop, apart from stall-outs. */
  readonly loopDraws: number;
  /** Games the action budget stopped, by what was looping (a card bug, not the deck). */
  readonly runaways: Readonly<Record<string, number>>;
  /** The hero's own turn in each game they won. */
  readonly winRounds: readonly number[];
  /** The same games in the engine's player-turn counter. */
  readonly winTurns: readonly number[];
  readonly winReasons: Readonly<Record<string, number>>;
  readonly lossReasons: Readonly<Record<string, number>>;
  /** Round -> games where the hero's commander first resolved then. */
  readonly commanderLanded: Readonly<Record<string, number>>;
  /** Round -> those games' indices: a bar here *is* a set of games. */
  readonly commanderLandedGames: Readonly<Record<string, readonly number[]>>;
  readonly commanderNever: number;
  readonly cards: readonly CardImpact[];
  /** Most-answered first. */
  readonly removal: readonly RemovalTarget[];
  readonly series: readonly TurnSeries[];
  /** Round -> indices of games the hero won on that round. */
  readonly byWinRound: Readonly<Record<string, readonly number[]>>;
  readonly crashed: readonly { readonly index: number; readonly seed: number; readonly error: string }[];
}

// -- card numbers (Python CardImpact properties) ------------------------------

export const winRateDrawn = (c: CardImpact): number => (c.drawn ? c.drawnWins / c.drawn : 0);
export const winRateNotDrawn = (c: CardImpact): number => (c.notDrawn ? c.notDrawnWins / c.notDrawn : 0);

/** The difference the card being in the game makes to winning. */
export const impact = (c: CardImpact): number => winRateDrawn(c) - winRateNotDrawn(c);

export const reliable = (c: CardImpact): boolean => c.drawn >= MIN_SAMPLE && c.notDrawn >= MIN_SAMPLE;

/**
 * A 95% interval half-width on the difference of two proportions (normal
 * approximation), so a 12% impact from 40 games doesn't read like one from
 * 4,000. 1 when either side has no games.
 */
export function margin(c: CardImpact): number {
  if (!c.drawn || !c.notDrawn) return 1;
  const a = winRateDrawn(c);
  const b = winRateNotDrawn(c);
  return 1.96 * Math.sqrt((a * (1 - a)) / c.drawn + (b * (1 - b)) / c.notDrawn);
}

// -- the report's own numbers -------------------------------------------------

export const winRate = (r: Report): number => (r.games ? r.wins / r.games : 0);
export const stallRate = (r: Report): number => (r.games ? r.stalls / r.games : 0);
const mean = (xs: readonly number[]): number => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
/** The number to show a person: which of their own turns they win on. */
export const averageWinRound = (r: Report): number => mean(r.winRounds);
export const averageWinTurn = (r: Report): number => mean(r.winTurns);

/** Reliable cards, most helpful first. Ties keep name order. */
export function topCards(r: Report, limit = 20): CardImpact[] {
  return r.cards.filter(reliable).sort((a, b) => impact(b) - impact(a)).slice(0, limit);
}

/** Reliable cards, most harmful first. */
export function worstCards(r: Report, limit = 10): CardImpact[] {
  return r.cards.filter(reliable).sort((a, b) => impact(a) - impact(b)).slice(0, limit);
}

// -- building it --------------------------------------------------------------

function bump(counter: Record<string, number>, key: string | number): void {
  counter[key] = (counter[key] ?? 0) + 1;
}

function push(groups: Record<string, number[]>, key: string | number, value: number): void {
  (groups[key] ??= []).push(value);
}

export interface ReportOptions {
  readonly hero?: number;
  readonly seats?: number;
}

export function buildReport(
  records: readonly GameRecord[],
  crashed: readonly CrashedGame[] = [],
  options: ReportOptions = {},
): Report {
  const hero = options.hero ?? 0;
  const seats = options.seats ?? (records.reduce((n, r) => Math.max(n, r.commanderLanded.length), 0) || 4);

  let wins = 0;
  let stalls = 0;
  let loopDraws = 0;
  const runaways: Record<string, number> = {};
  const winRounds: number[] = [];
  const winTurns: number[] = [];
  const winReasons: Record<string, number> = {};
  const lossReasons: Record<string, number> = {};
  const byWinRound: Record<string, number[]> = {};
  const commanderLanded: Record<string, number> = {};
  const commanderLandedGames: Record<string, number[]> = {};
  let commanderNever = 0;

  for (const record of records) {
    if (record.runaway) bump(runaways, record.runaway);
    // Outcomes (Python _outcomes).
    if (record.winner === null && record.loopDraw) {
      loopDraws += 1;
      bump(winReasons, "DRAW"); // Python LOOP_DRAW
    } else if (record.winner === null) {
      stalls += 1;
      bump(winReasons, "STALL_OUT");
    } else if (record.winner === hero) {
      wins += 1;
      winRounds.push(record.rounds);
      winTurns.push(record.turns);
      bump(winReasons, record.winReason);
      push(byWinRound, record.rounds, record.index);
    } else {
      const loss = record.eliminations.find((e) => e.player === hero);
      if (loss !== undefined) bump(lossReasons, loss.reason);
    }

    // The commander histogram (Python _commander).
    const landed = record.commanderLanded[hero];
    if (typeof landed === "number") {
      bump(commanderLanded, landed);
      push(commanderLandedGames, landed, record.index);
    } else {
      commanderNever += 1;
    }
  }

  return {
    games: records.length,
    hero,
    seats,
    wins,
    stalls,
    loopDraws,
    runaways,
    winRounds,
    winTurns,
    winReasons,
    lossReasons,
    commanderLanded,
    commanderLandedGames,
    commanderNever,
    cards: cardImpact(records, hero),
    removal: removalTargets(records),
    series: turnSeries(records),
    byWinRound,
    crashed: crashed.map(({ index, seed, error }) => ({ index, seed, error })),
  };
}

/**
 * Win rate when a card was drawn, against when it wasn't (Python
 * `_card_impact`). Both halves matter: a card in every win and every loss
 * has no measurable impact. A card drawn twice in one game counts once.
 */
function cardImpact(records: readonly GameRecord[], hero: number): CardImpact[] {
  const drawn = new Map<string, number>();
  const drawnWins = new Map<string, number>();
  const castCount = new Map<string, number>();
  const castRounds = new Map<string, number>();
  const opening = new Map<string, number>();
  const add = (map: Map<string, number>, key: string, by = 1): void => {
    map.set(key, (map.get(key) ?? 0) + by);
  };

  let totalWins = 0;
  for (const record of records) {
    const won = record.winner === hero;
    if (won) totalWins += 1;
    for (const name of new Set(record.drawn)) {
      add(drawn, name);
      if (won) add(drawnWins, name);
    }
    for (const name of record.openingHand) add(opening, name);
    for (const event of record.cast) {
      add(castCount, event.name);
      add(castRounds, event.name, event.round);
    }
  }

  const total = records.length;
  // Sorted by code unit, like Python's sorted(): stable across engines.
  return [...drawn.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)).map((name) => {
    const seen = drawn.get(name) ?? 0;
    const seenWins = drawnWins.get(name) ?? 0;
    const casts = castCount.get(name) ?? 0;
    return {
      name,
      drawn: seen,
      drawnWins: seenWins,
      notDrawn: total - seen,
      notDrawnWins: totalWins - seenWins,
      cast: casts,
      averageCastRound: casts ? (castRounds.get(name) ?? 0) / casts : 0,
      openingHand: opening.get(name) ?? 0,
    };
  });
}

/** Removal by victim, most losses first; ties keep first-seen order (Python `_removal`). */
function removalTargets(records: readonly GameRecord[]): RemovalTarget[] {
  const targets = new Map<string, { victim: string; losses: number; bySource: Record<string, number>; byMethod: Record<string, number> }>();
  for (const record of records) {
    for (const event of record.removal) {
      let target = targets.get(event.victim);
      if (target === undefined) targets.set(event.victim, (target = { victim: event.victim, losses: 0, bySource: {}, byMethod: {} }));
      target.losses += 1;
      bump(target.bySource, event.source);
      bump(target.byMethod, event.how);
    }
  }
  return [...targets.values()].sort((a, b) => b.losses - a.losses);
}

const cmp = (a: string | number, b: string | number): number => (a < b ? -1 : a > b ? 1 : 0);

/**
 * Each metric per player per turn, averaged over the games that reached
 * that turn (so a metric doesn't decay just because games ended), with up to
 * EXAMPLES_PER_POINT games behind each point and the one nearest its mean
 * (Python `_series`).
 */
function turnSeries(records: readonly GameRecord[]): TurnSeries[] {
  type Key = string;
  const keyOf = (metric: Metric, player: number, turn: number): Key => `${metric}|${player}|${turn}`;
  const totals = new Map<Key, { metric: Metric; player: number; turn: number; total: number; count: number }>();
  const examples = new Map<Key, number[]>();
  for (const record of records) {
    for (const snapshot of record.turnsSeries) {
      for (const metric of METRICS) {
        const key = keyOf(metric, snapshot.player, snapshot.turn);
        let bucket = totals.get(key);
        if (bucket === undefined) totals.set(key, (bucket = { metric, player: snapshot.player, turn: snapshot.turn, total: 0, count: 0 }));
        bucket.total += snapshot[metric];
        bucket.count += 1;
        let seen = examples.get(key);
        if (seen === undefined) examples.set(key, (seen = []));
        if (seen.length < EXAMPLES_PER_POINT) seen.push(record.index);
      }
    }
  }
  const means = new Map<Key, number>();
  for (const [key, b] of totals) means.set(key, b.count ? b.total / b.count : 0);

  // The game nearest each mean; ties to the lower index, so a rerun opens the same game.
  const nearest = new Map<Key, [number, number, number]>();
  for (const record of records) {
    for (const snapshot of record.turnsSeries) {
      for (const metric of METRICS) {
        const key = keyOf(metric, snapshot.player, snapshot.turn);
        const value = snapshot[metric];
        const distance = Math.abs(value - means.get(key)!);
        const best = nearest.get(key);
        if (best === undefined || distance < best[0] || (distance === best[0] && record.index < best[1])) {
          nearest.set(key, [distance, record.index, value]);
        }
      }
    }
  }

  // Python sorts the (metric, player, turn) keys; metric names compare as strings.
  const ordered = [...totals.values()].sort((a, b) => cmp(a.metric, b.metric) || a.player - b.player || a.turn - b.turn);
  const grouped = new Map<string, { metric: Metric; player: number; turns: number[]; values: number[]; samples: number[]; examples: number[][]; representative: number[]; representativeValue: number[] }>();
  for (const b of ordered) {
    const groupKey = `${b.metric}|${b.player}`;
    let series = grouped.get(groupKey);
    if (series === undefined) {
      grouped.set(
        groupKey,
        (series = { metric: b.metric, player: b.player, turns: [], values: [], samples: [], examples: [], representative: [], representativeValue: [] }),
      );
    }
    const key = keyOf(b.metric, b.player, b.turn);
    series.turns.push(b.turn);
    series.values.push(means.get(key)!);
    series.samples.push(b.count);
    series.examples.push(examples.get(key)!);
    const [, index, value] = nearest.get(key) ?? [0, -1, 0];
    series.representative.push(index);
    series.representativeValue.push(value);
  }
  return [...grouped.values()];
}
