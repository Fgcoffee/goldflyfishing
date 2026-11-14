/**
 * The JSON the website's Results and Replay screens read, built from a
 * browser run. The screens (mtgfish/ui/web/app.js) were written against
 * Python's `Bridge._report_payload` and `Bridge.replay`; these produce the
 * same shapes, field for field, so a browser run can be shown by the same
 * code as a server run.
 *
 * What a browser run doesn't have is said rather than invented: `loops` is
 * empty because game records don't carry the loops shortcut yet, and card
 * art is left to the page (Python looks it up in its card database).
 */

import type { CardDocument } from "../ir";
import {
  averageWinRound,
  averageWinTurn,
  impact,
  margin,
  reliable,
  stallRate,
  winRate,
  type Report,
} from "./report";
import { DETAIL_LEVELS, KEY_KINDS, NOISE_KINDS, type ReplayView } from "./replay_view";
import type { BoardCard, BoardSnapshot } from "./board";

export interface ResultsExtras {
  /** Cards none of whose abilities were read: `inertCards(setup.cards)`. */
  readonly unparsedCards?: readonly string[];
  /** Games whose replay gave a different digest (the page's replay check). */
  readonly digestMismatches?: readonly number[];
}

/** Python `Bridge._report_payload`. */
export function resultsPayload(report: Report, extras: ResultsExtras = {}): Record<string, unknown> {
  const commanderLandedGames: Record<string, number[]> = {};
  for (const [round, games] of Object.entries(report.commanderLandedGames)) commanderLandedGames[round] = games.slice(0, 20);
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
    commander_landed_games: commanderLandedGames,
    commander_landed: { ...report.commanderLanded },
    commander_never: report.commanderNever,
    by_win_round: Object.fromEntries(Object.entries(report.byWinRound).map(([k, v]) => [k, v.slice()])),
    runaways: { ...report.runaways },
    // Game records don't carry the loops shortcut (CR 732.2a) yet.
    loops: {},
    crashed: report.crashed.map((c) => ({ ...c })),
    loop_draws: report.loopDraws,
    unparsed_cards: [...(extras.unparsedCards ?? [])],
    digest_mismatches: [...(extras.digestMismatches ?? [])],
    // Python's order: by name.
    cards: [...report.cards]
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
      .map((c) => ({
        name: c.name,
        impact: impact(c),
        margin: margin(c),
        drawn: c.drawn,
        cast: c.cast,
        average_cast_turn: c.averageCastRound,
        reliable: reliable(c),
      })),
    removal: report.removal.slice(0, 30).map((t) => ({
      victim: t.victim,
      losses: t.losses,
      by_source: { ...t.bySource },
      by_method: { ...t.byMethod },
    })),
    series: report.series.map((s) => ({
      metric: s.metric,
      player: s.player,
      turns: s.turns.slice(),
      values: s.values.slice(),
      samples: s.samples.slice(),
      examples: s.examples.map((e) => e.slice()),
      representative: s.representative.slice(),
      representative_value: s.representativeValue.slice(),
    })),
  };
}

/** Every ability on every face of a card document. */
function abilitiesOf(doc: CardDocument | undefined): readonly { unparsed?: boolean; text?: string }[] {
  return (doc?.faces ?? []).flatMap((f) => (f.abilities ?? []) as readonly { unparsed?: boolean; text?: string }[]);
}

/** An ability the parser didn't read, or whose effects hold an unread node (Python's walk for `is_unparsed`). */
function unreadAbility(a: { unparsed?: boolean }): boolean {
  return a.unparsed === true || JSON.stringify(a).includes('"unparsed":true');
}

/**
 * Cards none of whose abilities were read (the report's "inert cards"), by
 * name: of the whole run, or of one deck when `oracleIds` names its cards.
 */
export function inertCards(cards: Readonly<Record<string, CardDocument>>, oracleIds?: Iterable<string>): string[] {
  const ids = oracleIds === undefined ? Object.keys(cards) : [...new Set(oracleIds)];
  const out: string[] = [];
  for (const id of ids) {
    const doc = cards[id];
    if (doc !== undefined && parseStatus(doc).status === "blank") out.push(doc.name);
  }
  return out.sort();
}

/**
 * Python `Bridge.replay`, less the card pictures: `board_cards` carries each
 * card's name, oracle id, type line, mana cost and parse status (from the
 * run's card IR, `cards`), and the page adds art.
 * The boards themselves go one at a time (`boardPayload`), as Python sends
 * them, though in the browser that is only to keep the first answer small.
 */
export function replayPayload(view: ReplayView, cards: Readonly<Record<string, CardDocument>> = {}): Record<string, unknown> {
  return {
    game_index: view.game_index,
    seed: view.seed,
    turns: view.turns,
    winner: view.winner,
    outcome: view.outcome,
    final_board: view.final_board,
    detail_levels: [...DETAIL_LEVELS],
    key_kinds: [...KEY_KINDS].sort(),
    noise_kinds: [...NOISE_KINDS].sort(),
    seats: view.seats.map((s) => ({ ...s })),
    turns_taken: view.turn_list.map((t) => ({ ...t })),
    frames: view.frames.map((f) => ({
      turn: f.turn,
      phase: f.phase,
      step: f.step,
      player: f.player,
      kind: f.kind,
      text: f.text,
      depth: f.depth,
      active_player: f.active_player,
      player_turn: f.player_turn,
    })),
    board_cards: view.film.cards.map((c) => ({
      name: c.name,
      oracle_id: c.oracle_id,
      type_line: c.type_line,
      mana_cost: c.mana_cost,
      token: c.token,
      // Python looks pictures up in its card database; the page fills these in.
      art: "",
      image: "",
      ...parseStatus(cards[c.oracle_id]),
    })),
    board_frames: view.film.snapshots.map((s) => s.frame),
    digest: view.digest,
  };
}

/**
 * Python `deckview.parse_status`, from the IR alone: "blank" when nothing on
 * the card was read, "partial" with the unread abilities' text, else "ok".
 * (Python also counts abilities the sandbox saw never fire; the IR can't.)
 */
export function parseStatus(doc: CardDocument | undefined): { status: string; unread: string[] } {
  const abilities = abilitiesOf(doc);
  const unread = abilities.filter(unreadAbility).map((a) => a.text ?? "");
  // A vanilla creature or a basic land is not a gap.
  if (abilities.length === 0) return { status: "ok", unread };
  if (unread.length === abilities.length) return { status: "blank", unread };
  return { status: unread.length > 0 ? "partial" : "ok", unread };
}

/** Python's bit flags for a permanent on the wire. */
export const TAPPED = 1, SICK = 2, TOKEN = 4, COMMANDER = 8, FACE_DOWN = 16, PHASED_OUT = 32, UNREADABLE = 64;

/** Python `_permanent_payload`: `[id, card, flags, power, toughness, damage, extra?]`. */
function permanentPayload(c: BoardCard): unknown[] {
  const flags =
    (c.tapped ? TAPPED : 0) | (c.sick ? SICK : 0) | (c.token ? TOKEN : 0) | (c.commander ? COMMANDER : 0) |
    (c.face_down ? FACE_DOWN : 0) | (c.phased_out ? PHASED_OUT : 0) | (c.unreadable ? UNREADABLE : 0);
  const row: unknown[] = [c.id, c.card, flags, c.power, c.toughness, c.damage];
  const extra: Record<string, unknown> = {};
  if (Object.keys(c.counters).length > 0) extra.c = { ...c.counters };
  if (c.attacking >= 0) extra.a = c.attacking;
  if (c.attacking_permanent >= 0) extra.ap = c.attacking_permanent;
  if (c.blocking.length > 0) extra.b = c.blocking.slice();
  if (c.attached_to >= 0) extra.at = c.attached_to;
  if (Object.keys(extra).length > 0) row.push(extra);
  return row;
}

/** Python `Bridge.replay_board` / `_board_payload`: one board of the film, by index, clamped. */
export function boardPayload(view: ReplayView, index: number): Record<string, unknown> {
  const snapshots = view.film.snapshots;
  if (snapshots.length === 0) return { error: "no replay yet" };
  const i = Math.max(0, Math.min(Math.trunc(index), snapshots.length - 1));
  const s = snapshots[i] as BoardSnapshot;
  return {
    index: i,
    frame: s.frame,
    turn: s.turn,
    phase: s.phase,
    step: s.step,
    active: s.active,
    seats: s.seats.map((seat) => ({
      player: seat.player,
      life: seat.life,
      hand: seat.hand,
      library: seat.library,
      graveyard: seat.graveyard,
      exile: seat.exile,
      poison: seat.poison,
      out: seat.out,
      permanents: seat.permanents.map(permanentPayload),
      hand_cards: seat.hand_cards.map(permanentPayload),
      graveyard_cards: seat.graveyard_cards.map(permanentPayload),
    })),
    stack: s.stack.map((item) => ({ card: item.card, controller: item.controller, text: item.text })),
  };
}
