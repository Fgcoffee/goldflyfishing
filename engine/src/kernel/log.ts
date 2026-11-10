/**
 * The game log, which is also the replay. Ported from
 * mtgfish/rules/kernel/log.py.
 *
 * During a run logging is off and `record` only updates the digest; during a
 * replay every entry is kept. The digest is always maintained: the same seed
 * must give the same digest, or replay is a lie.
 */

import { cloneDigest, type Digest, hexDigest, newDigest, updateDigest } from "./digest";
import { stepName, type Phase, type Step } from "./enums";
import { NO_PLAYER, type PlayerId } from "./ids";

export interface LogEntry {
  readonly turn: number;
  readonly phase: Phase;
  readonly step: Step;
  /** Whose entry this is - the player who cast, drew, lost. Often nobody. */
  readonly player: PlayerId;
  readonly kind: string;
  readonly text: string;
  /** Nesting depth, for indenting a resolution inside another. */
  readonly depth: number;
  /** Whose turn it was, which is not the same as `player`. */
  readonly activePlayer: PlayerId;
}

export interface GameLog {
  enabled: boolean;
  entries: LogEntry[];
  depth: number;
  digest: Digest;
}

/** What `record` reads from the game: when it is happening. */
export interface LogClock {
  readonly turn: number;
  readonly phase: Phase;
  readonly step: Step;
  readonly activePlayer: PlayerId;
}

export function newGameLog(enabled = false): GameLog {
  return { enabled, entries: [], depth: 0, digest: newDigest() };
}

export function cloneGameLog(log: GameLog): GameLog {
  return {
    enabled: log.enabled,
    entries: log.entries.slice(),
    depth: log.depth,
    digest: cloneDigest(log.digest),
  };
}

export function record(
  log: GameLog,
  clock: LogClock,
  text: string,
  options: { kind?: string; player?: PlayerId } = {},
): void {
  const kind = options.kind ?? "info";
  const player = options.player ?? NO_PLAYER;
  updateDigest(log.digest, `${clock.turn}|${clock.step}|${player}|${kind}|${text}\n`);
  if (!log.enabled) return;
  log.entries.push({
    turn: clock.turn,
    phase: clock.phase,
    step: clock.step,
    player,
    kind,
    text,
    depth: log.depth,
    activePlayer: clock.activePlayer,
  });
}

export function pushDepth(log: GameLog): void {
  log.depth += 1;
}

export function popDepth(log: GameLog): void {
  log.depth = Math.max(0, log.depth - 1);
}

/** Hash of everything recorded so far. */
export function logDigest(log: GameLog): string {
  return hexDigest(log.digest);
}

export function entryText(entry: LogEntry): string {
  const indent = "  ".repeat(entry.depth);
  return `T${entry.turn} ${stepName(entry.step).toLowerCase().padEnd(24)} ${indent}${entry.text}`;
}

export function logText(log: GameLog): string {
  return log.entries.map(entryText).join("\n");
}

export function entriesForTurn(log: GameLog, turn: number): LogEntry[] {
  return log.entries.filter((e) => e.turn === turn);
}
