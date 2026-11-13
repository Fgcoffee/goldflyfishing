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
 * Still to port, once the engine records the data: per-turn series
 * (`TurnSeries`), removal ("what do I lose to"), runaways and loop draws.
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

export interface Report {
  readonly games: number;
  readonly hero: number;
  readonly seats: number;
  readonly wins: number;
  readonly stalls: number;
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
  const winRounds: number[] = [];
  const winTurns: number[] = [];
  const winReasons: Record<string, number> = {};
  const lossReasons: Record<string, number> = {};
  const byWinRound: Record<string, number[]> = {};
  const commanderLanded: Record<string, number> = {};
  const commanderLandedGames: Record<string, number[]> = {};
  let commanderNever = 0;

  for (const record of records) {
    // Outcomes (Python _outcomes).
    if (record.winner === null) {
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
    winRounds,
    winTurns,
    winReasons,
    lossReasons,
    commanderLanded,
    commanderLandedGames,
    commanderNever,
    cards: cardImpact(records, hero),
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
