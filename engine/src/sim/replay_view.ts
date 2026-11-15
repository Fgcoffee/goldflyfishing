/**
 * Reconstructing one game, turn by turn, for the replay screen. Ported from
 * mtgfish/sim/replay.py.
 *
 * Nothing is loaded: the game is played again from its seed, with the full
 * log and the board film on, and comes out the same game. The output is the
 * JSON the replay screen reads (Python `Bridge.replay`), so the screen can
 * show a browser run's game exactly as it shows a server run's.
 *
 * Turns are numbered per player ("Opponent 2, their turn 4"), with the
 * engine's global counter kept alongside; entries are graded by detail level
 * rather than dropped.
 */

import { lossReasonName, phaseName, stepName } from "../kernel/enums";
import { NO_PLAYER } from "../kernel/ids";
import { permanents, type Game } from "../kernel/game";
import type { LogEntry } from "../kernel/log";
import type { GameObject } from "../kernel/gameobject";
import { objectText } from "../kernel/gameobject";
import { playerText } from "../rules/cr100_game_concepts/player";
import { characteristicsOf } from "../rules/cr600_spells_and_abilities/layers";
import { playGame } from "../rules/play";
import { BoardRecorder, type BoardFilm } from "./board";
import type { TurnSnapshot } from "./records";
import type { RunSetup } from "./runner";

/** The kinds that carry the story of a game: "Key moments" shows these and nothing else. */
export const KEY_KINDS: ReadonlySet<string> = new Set([
  "setup", "turn", "cast", "land", "loss", "combat", "commander", "loop", "solved", "stall",
  "runaway", "restart", "mulligan", "emblem", "monarch", "initiative", "transform", "flip",
]);

/** The raw event stream, hidden unless asked for. */
export const NOISE_KINDS: ReadonlySet<string> = new Set(["event"]);

/** Least to most. An unclassified kind shows at "normal". */
export const DETAIL_LEVELS = ["key", "normal", "everything"] as const;
export type DetailLevel = (typeof DETAIL_LEVELS)[number];

export function shownAt(kind: string, level: DetailLevel): boolean {
  if (level === "everything") return true;
  if (NOISE_KINDS.has(kind)) return false;
  if (level === "key") return KEY_KINDS.has(kind);
  return true;
}

/** One logged moment (Python `ReplayFrame`; JSON field names as the screen reads them). */
export interface ReplayFrame {
  readonly index: number;
  readonly turn: number;
  readonly phase: string;
  readonly step: string;
  readonly player: number;
  readonly kind: string;
  readonly text: string;
  readonly depth: number;
  /** Whose turn it was, and that seat's own number for it. */
  readonly active_player: number;
  readonly player_turn: number;
}

export interface ReplaySeat {
  readonly id: number;
  readonly name: string;
  readonly won: boolean;
  readonly left_on_turn: number;
  readonly loss_reason: string;
  turns_taken: number;
  readonly is_hero: boolean;
}

/** One player's turn: who took it, which of theirs it was, what it held. */
export interface ReplayTurn {
  readonly turn: number;
  readonly player: number;
  readonly player_name: string;
  /** This seat's own count of their turns; 0 for the setup block. */
  readonly player_turn: number;
  readonly start: number;
  end: number;
  readonly is_setup: boolean;
  spells_cast: number;
  lands_played: number;
  life: number | null;
  lands: number | null;
  cards_in_hand: number | null;
  permanents: number | null;
  board_power: number | null;
  readonly label: string;
}

export interface ReplayView {
  readonly game_index: number;
  readonly seed: number;
  readonly turns: number;
  readonly winner: number | null;
  readonly frames: ReplayFrame[];
  /** Frame index where each global turn starts. */
  readonly turn_starts: Record<number, number>;
  readonly turn_list: ReplayTurn[];
  readonly seats: ReplaySeat[];
  readonly final_board: string;
  readonly outcome: string;
  readonly film: BoardFilm;
  /** The digest of the replayed game, to check against the run's record. */
  readonly digest: string;
}

/**
 * Replay game `index` of a run with full logging. `boards` also records the
 * film the viewer draws; off, the text log alone is a good deal cheaper.
 */
export function replayGame(setup: RunSetup, index: number, seed: number, options: { boards?: boolean; hero?: number } = {}): ReplayView {
  const hero = options.hero ?? 0;
  const recorder = (options.boards ?? true) ? new BoardRecorder() : null;
  const { record, game } = playGame(setup, index, seed, { log: true, ...(recorder !== null ? { watch: recorder.watch } : {}) });

  const seats = seatsOf(game, hero);
  const names = new Map(seats.map((s) => [s.id, s.name]));
  const snapshots = new Map(record.turnsSeries.map((s) => [`${s.player}|${s.turn}`, s]));
  const { frames, turns, starts } = buildTurns(game.log.entries, names, snapshots);
  const taken = new Map<number, number>();
  for (const t of turns) taken.set(t.player, t.player_turn);
  for (const seat of seats) seat.turns_taken = taken.get(seat.id) ?? 0;

  // One last sample: the board a viewer most wants is the one the game ended on.
  recorder?.sample(game);
  const partial = {
    game_index: index,
    seed: record.seed,
    turns: record.turns,
    winner: record.winner,
    frames,
    turn_starts: starts,
    turn_list: turns,
    seats,
    final_board: describeBoard(game),
    film: recorder?.film ?? { cards: [], snapshots: [] },
    digest: record.digest,
  };
  return { ...partial, outcome: outcomeOf(partial.winner, seats, game) };
}

/**
 * Group a log into frames and the turns they belong to (Python
 * `build_turns`). A turn ends where the global counter moves; the seat that
 * took it is read off the entry's active player, not inferred from `player`.
 */
export function buildTurns(
  entries: readonly LogEntry[],
  names: ReadonlyMap<number, string>,
  snapshots: ReadonlyMap<string, TurnSnapshot> = new Map(),
): { frames: ReplayFrame[]; turns: ReplayTurn[]; starts: Record<number, number> } {
  const frames: ReplayFrame[] = [];
  const turns: ReplayTurn[] = [];
  const starts: Record<number, number> = {};
  const ownTurns = new Map<number, number>();
  let current: ReplayTurn | null = null;

  entries.forEach((entry, position) => {
    const active = entry.activePlayer;
    if (current === null || entry.turn !== current.turn) {
      if (current !== null) current.end = position;
      // Turn zero is setup: shuffles, opening hands, mulligans. Nobody's turn.
      const setup = entry.turn <= 0;
      if (!setup) ownTurns.set(active, (ownTurns.get(active) ?? 0) + 1);
      const playerName = setup ? "" : (names.get(active) ?? seatName(active));
      const playerTurn = setup ? 0 : ownTurns.get(active)!;
      const turn: ReplayTurn = {
        turn: entry.turn,
        player: setup ? -1 : active,
        player_name: playerName,
        player_turn: playerTurn,
        start: position,
        end: position,
        is_setup: setup,
        spells_cast: 0,
        lands_played: 0,
        life: null,
        lands: null,
        cards_in_hand: null,
        permanents: null,
        board_power: null,
        label: setup ? "Before the game" : `${playerName} - turn ${playerTurn}`,
      };
      if (!setup) {
        // Keyed by seat and that seat's own turn, as the observer numbers its series.
        const snap = snapshots.get(`${turn.player}|${turn.player_turn}`);
        if (snap !== undefined) {
          turn.life = snap.life;
          turn.lands = snap.lands;
          turn.cards_in_hand = snap.cardsInHand;
          turn.permanents = snap.permanents;
          turn.board_power = snap.boardPower;
        }
      }
      turns.push(turn);
      if (!(entry.turn in starts)) starts[entry.turn] = position;
      current = turn;
    }
    const turn = current as ReplayTurn;
    if (entry.kind === "cast") turn.spells_cast += 1;
    else if (entry.kind === "land") turn.lands_played += 1;
    frames.push({
      index: position,
      turn: entry.turn,
      phase: phaseName(entry.phase),
      step: stepName(entry.step),
      player: entry.player,
      kind: entry.kind,
      text: entry.text,
      depth: entry.depth,
      active_player: active,
      player_turn: turn.player_turn,
    });
  });
  if (current !== null) (current as ReplayTurn).end = frames.length;
  return { frames, turns, starts };
}

function seatName(player: number): string {
  return player === NO_PLAYER ? "nobody" : `P${player}`;
}

function seatsOf(game: Game, hero: number): ReplaySeat[] {
  const winners = new Set(game.winners);
  return game.players.map((p) => ({
    id: p.id,
    name: p.name || `P${p.id}`,
    won: winners.has(p.id),
    left_on_turn: p.hasLost ? p.leftOnTurn : -1,
    loss_reason: p.lossReason !== null ? lossReasonName(p.lossReason) : "",
    turns_taken: 0,
    is_hero: p.id === hero,
  }));
}

/** Python `Game.describe_board`. */
export function describeBoard(game: Game): string {
  const text = (obj: GameObject) => objectText(obj, characteristicsOf(game, obj).name);
  const lines = [`Turn ${game.turn} - ${stepName(game.step)} - active P${game.activePlayer}`];
  for (const p of game.players) {
    lines.push(`  ${playerText(p)}  hand=${p.hand.length} library=${p.library.length}`);
    const controlled = permanents(game, p.id).map(text);
    if (controlled.length > 0) lines.push("    " + controlled.join(", "));
  }
  if (game.stack.length > 0) {
    lines.push("  Stack: " + [...game.stack].reverse().map((id) => text(game.objects.get(id)!)).join(", "));
  }
  return lines.join("\n");
}

/** How the game ended, in the words the log already used (Python `_outcome`). */
function outcomeOf(winner: number | null, seats: readonly ReplaySeat[], game: Game): string {
  for (let i = game.log.entries.length - 1; i >= 0; i--) {
    const entry = game.log.entries[i]!;
    if (entry.kind === "stall" || entry.kind === "runaway" || entry.kind === "restart") return entry.text;
  }
  if (winner === null) {
    if (game.loopDraw) return "a draw: a mandatory loop nothing could break (CR 104.4b)";
    return "nobody won";
  }
  const won = seats.find((s) => s.id === winner);
  const losers = seats.filter((s) => s.loss_reason);
  const how = losers.length > 0 ? losers[losers.length - 1]!.loss_reason.toLowerCase().replaceAll("_", " ") : "";
  const name = won !== undefined ? won.name : seatName(winner);
  return `${name} won` + (how ? ` - last player out to ${how}` : "");
}
