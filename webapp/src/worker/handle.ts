/**
 * What a simulation worker does with its messages, without the worker.
 *
 * Kept apart from the worker entry point so it can be tested in Node: the
 * entry point only wires `receive` to `onmessage` and `post` to
 * `postMessage`.
 */

import { ENGINE_VERSION } from "@goldfly/engine";
import { ENGINES, runGames } from "@goldfly/engine/sim";
import { isRequest, type Reply, type RunRequest } from "./protocol";

/** How often a running worker reports progress and reads new messages. */
const PROGRESS_EVERY = 5;

/** Let queued messages (a cancel) run before the next game. */
const yieldToMessages = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

export class WorkerHandler {
  /** Runs in progress, by request id, with their stop flag. */
  private readonly running = new Map<number, { stop: boolean }>();

  constructor(private readonly post: (reply: Reply) => void) {}

  /** Handle one message. Resolves when its work, if any, is done. */
  async receive(message: unknown): Promise<void> {
    if (!isRequest(message)) {
      this.post({ type: "error", id: idOf(message), message: "not a request this worker understands" });
      return;
    }
    switch (message.type) {
      case "ping":
        this.post({ type: "pong", id: message.id, payload: message.payload, engineVersion: ENGINE_VERSION });
        return;
      case "cancel": {
        const run = this.running.get(message.run);
        if (run !== undefined) run.stop = true;
        return;
      }
      case "run":
        await this.run(message);
        return;
    }
  }

  private async run(request: RunRequest): Promise<void> {
    const flag = { stop: false };
    this.running.set(request.id, flag);
    try {
      const result = await runGames(ENGINES[request.engine], request.setup, request.start, request.end, {
        shouldStop: () => flag.stop,
        yieldEvery: PROGRESS_EVERY,
        pause: yieldToMessages,
        onGame: (done, total) => {
          if (done % PROGRESS_EVERY === 0 || done === total) {
            this.post({ type: "progress", id: request.id, done, total });
          }
        },
      });
      this.post({ type: "result", id: request.id, result });
    } catch (error) {
      this.post({
        type: "error",
        id: request.id,
        message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    } finally {
      this.running.delete(request.id);
    }
  }
}

function idOf(message: unknown): number {
  if (typeof message === "object" && message !== null) {
    const id = (message as { id?: unknown }).id;
    if (typeof id === "number" && Number.isInteger(id)) return id;
  }
  return -1;
}
