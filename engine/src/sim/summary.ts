/**
 * The headline numbers of a run, per seat. A first, small slice of Python
 * `mtgfish/sim/stats.py` `summarize`: enough for a result table. The full
 * port (card impact, series, removal) follows once real games produce the
 * data.
 */

import type { CrashedGame, GameRecord } from "./records";

export interface SeatSummary {
  readonly seat: number;
  readonly wins: number;
  readonly winRate: number;
  /** Mean round of this seat's wins, or null if it never won. */
  readonly averageWinRound: number | null;
  /** Games where this seat's commander resolved at least once. */
  readonly commanderLanded: number;
  /** Mean round the commander first resolved, or null if it never did. */
  readonly averageCommanderRound: number | null;
  /** Share of games with at least one mulligan. */
  readonly mulliganRate: number;
}

export interface RunSummary {
  readonly games: number;
  readonly crashed: number;
  readonly stalls: number;
  readonly stallRate: number;
  readonly seats: readonly SeatSummary[];
}

const mean = (values: readonly number[]): number | null =>
  values.length === 0 ? null : values.reduce((a, b) => a + b, 0) / values.length;

export function summarize(records: readonly GameRecord[], crashed: readonly CrashedGame[] = []): RunSummary {
  const games = records.length;
  const seatCount = records.reduce((n, r) => Math.max(n, r.commanderLanded.length, r.mulligans.length), 0);
  const stalls = records.filter((r) => r.winner === null).length;

  const seats: SeatSummary[] = [];
  for (let seat = 0; seat < seatCount; seat += 1) {
    const won = records.filter((r) => r.winner === seat);
    const landed = records
      .map((r) => r.commanderLanded[seat])
      .filter((round): round is number => typeof round === "number");
    seats.push({
      seat,
      wins: won.length,
      winRate: games === 0 ? 0 : won.length / games,
      averageWinRound: mean(won.map((r) => r.rounds)),
      commanderLanded: landed.length,
      averageCommanderRound: mean(landed),
      mulliganRate: games === 0 ? 0 : records.filter((r) => (r.mulligans[seat] ?? 0) > 0).length / games,
    });
  }

  return { games, crashed: crashed.length, stalls, stallRate: games === 0 ? 0 : stalls / games, seats };
}
