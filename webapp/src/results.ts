/**
 * A browser run's report in the shape the website's Results screen draws:
 * the same JSON `mtgfish/ui/bridge.py` `_report_payload` builds for a server
 * run, so mtgfish/ui/web/app.js can show either without knowing which.
 *
 * TS field names are camelCase; the payload keeps Python's snake_case, and
 * the per-turn metrics keep Python's names ("mana_available", ...).
 */

import type { CardDocument } from "@goldfly/engine/ir";
import {
  averageWinRound, averageWinTurn, impact, margin, reliable, stallRate, winRate, type Report,
} from "@goldfly/engine/sim";

/** TS metric name -> the Python name the Results charts look up. */
const METRIC_NAMES: Record<string, string> = {
  life: "life",
  lands: "lands",
  manaAvailable: "mana_available",
  cardsInHand: "cards_in_hand",
  permanents: "permanents",
  boardPower: "board_power",
  creatures: "creatures",
};

/** Bars on the commander chart keep at most this many clickable games. */
const GAMES_PER_BAR = 20;
/** The removal table shows the top targets only, as the server does. */
const REMOVAL_ROWS = 30;

export interface PayloadExtras {
  /** Cards in the hero's deck the parser couldn't fully read. */
  readonly unparsedCards?: readonly string[];
  /** Games whose replay didn't reproduce their digest. */
  readonly digestMismatches?: readonly number[];
}

export function reportPayload(report: Report, extras: PayloadExtras = {}): Record<string, unknown> {
  return {
    games: report.games,
    seats: report.seats,
    win_rate: winRate(report),
    stall_rate: stallRate(report),
    wins: report.wins,
    stalls: report.stalls,
    average_win_round: averageWinRound(report),
    average_win_turn: averageWinTurn(report),
    win_reasons: { ...report.winReasons },
    loss_reasons: { ...report.lossReasons },
    commander_landed_games: Object.fromEntries(
      Object.entries(report.commanderLandedGames).map(([turn, games]) => [turn, games.slice(0, GAMES_PER_BAR)]),
    ),
    commander_landed: { ...report.commanderLanded },
    commander_never: report.commanderNever,
    by_win_round: { ...report.byWinRound },
    runaways: { ...report.runaways },
    // Loops noticed and shortcut: the TS engine doesn't record them yet
    // (rules/loops.ts is still a placeholder), so none are reported.
    loops: {},
    crashed: report.crashed.map((c) => ({ ...c })),
    loop_draws: report.loopDraws,
    unparsed_cards: [...(extras.unparsedCards ?? [])],
    digest_mismatches: [...(extras.digestMismatches ?? [])],
    cards: report.cards.map((card) => ({
      name: card.name,
      impact: impact(card),
      margin: margin(card),
      drawn: card.drawn,
      cast: card.cast,
      average_cast_turn: card.averageCastRound,
      reliable: reliable(card),
    })),
    removal: report.removal.slice(0, REMOVAL_ROWS).map((target) => ({
      victim: target.victim,
      losses: target.losses,
      by_source: { ...target.bySource },
      by_method: { ...target.byMethod },
    })),
    series: report.series.map((series) => ({
      metric: METRIC_NAMES[series.metric] ?? series.metric,
      player: series.player,
      turns: [...series.turns],
      values: [...series.values],
      samples: [...series.samples],
      examples: series.examples.map((e) => [...e]),
      representative: [...series.representative],
      representative_value: [...series.representativeValue],
    })),
  };
}

/** Names of cards in a deck whose IR has an ability the parser couldn't read. */
export function unparsedCards(
  deck: { readonly commanders: readonly string[]; readonly cards: readonly { readonly oracle_id: string }[] },
  cards: ReadonlyMap<string, CardDocument>,
): string[] {
  const ids = [...deck.commanders, ...deck.cards.map((c) => c.oracle_id)];
  const names = new Set<string>();
  for (const id of ids) {
    const card = cards.get(id);
    if (card !== undefined && card.faces.some((face) => face.fully_read === false)) names.add(card.name);
  }
  return [...names].sort();
}
