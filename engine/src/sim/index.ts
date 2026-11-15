/** Running games: records, the range runner, and the stub engine. */
export type { CardCast, CrashedGame, Elimination, GameRecord, WinReason } from "./records";
export type { DeckSpec, GameEngine, RunHooks, RunResult, RunSetup } from "./runner";
export { crashedGame, mergeResults, runGames, splitRange } from "./runner";
export { stubEngine } from "./stub";
export { DEFAULT_ENGINE, ENGINES, type EngineName } from "./engines";
export type { CardImpact, Report, ReportOptions } from "./report";
export {
  MIN_SAMPLE, averageWinRound, averageWinTurn, buildReport, impact, margin, reliable, stallRate, topCards,
  winRate, winRateDrawn, winRateNotDrawn, worstCards,
} from "./report";
export type { RunSummary, SeatSummary } from "./summary";
export { summarize } from "./summary";
export { atFrame, BoardRecorder, type BoardCard, type BoardCardRef, type BoardFilm, type BoardSeat, type BoardSnapshot, type BoardStackItem } from "./board";
export { buildTurns, describeBoard, DETAIL_LEVELS, KEY_KINDS, NOISE_KINDS, replayGame, shownAt, type DetailLevel, type ReplayFrame, type ReplaySeat, type ReplayTurn, type ReplayView } from "./replay_view";
export { boardPayload, parseStatus, replayPayload } from "./payload";
