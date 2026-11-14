import type { Game } from "../../src/kernel/game";
import { PassingAgent } from "../../src/rules/cr100_game_concepts/priority";

/** Seat an agent that passes, with some methods overridden. */
export function setAgent(game: Game, player: number, overrides: Record<string, unknown>): void {
  game.agents.set(player, Object.assign(new PassingAgent(), overrides));
}
