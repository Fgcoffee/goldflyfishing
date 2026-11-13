/** Running games: records, the range runner, and the stub engine. */
export type { CardCast, CrashedGame, Elimination, GameRecord, WinReason } from "./records";
export type { DeckSpec, GameEngine, RunHooks, RunResult, RunSetup } from "./runner";
export { crashedGame, mergeResults, runGames, splitRange } from "./runner";
export { stubEngine } from "./stub";
export type { CardImpact, Report, ReportOptions } from "./report";
export {
  MIN_SAMPLE, averageWinRound, averageWinTurn, buildReport, impact, margin, reliable, stallRate, topCards,
  winRate, winRateDrawn, winRateNotDrawn, worstCards,
} from "./report";
export type { RunSummary, SeatSummary } from "./summary";
export { summarize } from "./summary";
