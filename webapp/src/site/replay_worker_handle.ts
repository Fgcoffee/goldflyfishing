/**
 * What the replay worker does, without the worker, so Node can test it:
 * play the game again and keep its film, then hand out boards from it.
 */

import { boardPayload, replayGame, replayPayload, type ReplayView } from "@goldfly/engine/sim";
import type { ReplayReply, ReplayRequest } from "./replay_protocol";

export class ReplayHandler {
  private view: ReplayView | null = null;

  handle(request: ReplayRequest): ReplayReply {
    try {
      if (request.type === "replay") {
        this.view = replayGame(request.setup, request.index, request.seed, { hero: request.hero });
        return { id: request.id, payload: replayPayload(this.view, request.setup.cards) };
      }
      if (this.view === null) return { id: request.id, payload: { error: "no replay yet" } };
      return { id: request.id, payload: boardPayload(this.view, request.index) };
    } catch (error) {
      const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      return { id: request.id, payload: { error: `the replay crashed: ${message}` } };
    }
  }
}
