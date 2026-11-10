/**
 * The page's side of a simulation worker: requests in, promises out.
 */

import type { RunResult, RunSetup } from "@goldfly/engine/sim";
import type { PongReply, Reply, Request, RunRequest } from "./worker/protocol";

/** The part of `Worker` the client uses, so tests can pass a fake. */
export interface WorkerLike {
  postMessage(message: unknown): void;
  addEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void;
  addEventListener(type: "error", listener: (event: ErrorEvent) => void): void;
  terminate(): void;
}

/** Start a real worker. Vite bundles the worker from this exact expression. */
export function startWorker(): WorkerLike {
  return new Worker(new URL("./worker/sim.worker.ts", import.meta.url), {
    type: "module",
  });
}

type Pending = {
  resolve: (reply: Reply) => void;
  reject: (error: Error) => void;
  progress?: (done: number, total: number) => void;
};

export interface RunHandle {
  /** The request id, for `cancel`. */
  readonly id: number;
  /** Resolves when the slice finishes or stops; `complete` says which. */
  readonly result: Promise<RunResult>;
}

export class EngineClient {
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();

  constructor(private readonly worker: WorkerLike = startWorker()) {
    worker.addEventListener("message", (event) => this.receive(event.data));
    worker.addEventListener("error", (event) => this.failAll(event.message || "worker failed"));
  }

  /** Round-trip a value through the worker; resolves with the engine version. */
  async ping(payload: unknown = null): Promise<PongReply> {
    const reply = await this.request({ type: "ping", id: this.nextId++, payload });
    if (reply.type !== "pong") throw new Error(`expected pong, got ${reply.type}`);
    return reply;
  }

  /** Play games `start` to `end - 1` of a run in this worker. */
  run(
    setup: RunSetup,
    start: number,
    end: number,
    onProgress?: (done: number, total: number) => void,
    engine: RunRequest["engine"] = "stub",
  ): RunHandle {
    const id = this.nextId++;
    const result = this.request({ type: "run", id, setup, start, end, engine }, onProgress).then(
      (reply) => {
        if (reply.type !== "result") throw new Error(`expected result, got ${reply.type}`);
        return reply.result;
      },
    );
    return { id, result };
  }

  /** Ask a run to stop after its current game. Its promise still resolves. */
  cancel(run: number): void {
    this.worker.postMessage({ type: "cancel", id: this.nextId++, run } satisfies Request);
  }

  terminate(): void {
    this.worker.terminate();
    this.failAll("worker terminated");
  }

  private request(message: Request, progress?: Pending["progress"]): Promise<Reply> {
    return new Promise((resolve, reject) => {
      this.pending.set(message.id, { resolve, reject, progress });
      this.worker.postMessage(message);
    });
  }

  private receive(data: unknown): void {
    const reply = data as Reply;
    const waiting = this.pending.get(reply.id);
    if (waiting === undefined) return;
    if (reply.type === "progress") {
      waiting.progress?.(reply.done, reply.total);
      return;
    }
    this.pending.delete(reply.id);
    if (reply.type === "error") waiting.reject(new Error(reply.message));
    else waiting.resolve(reply);
  }

  private failAll(message: string): void {
    for (const waiting of this.pending.values()) waiting.reject(new Error(message));
    this.pending.clear();
  }
}
