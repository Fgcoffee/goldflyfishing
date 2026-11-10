/**
 * The messages between the page and a simulation worker.
 *
 * Every request carries an `id`, and every reply to it echoes that id, so
 * the page can have several requests in flight and match each reply to its
 * promise. Only plain data crosses the boundary (structured clone), never
 * class instances.
 *
 *   ping   -> pong                      the worker loaded the engine
 *   run    -> progress* -> result       play games [start, end) of a run
 *   cancel -> (the run's result, with complete: false)
 *
 * Any request can instead get an `error` reply.
 */

import type { RunResult, RunSetup } from "@goldfly/engine/sim";

export interface PingRequest {
  readonly type: "ping";
  readonly id: number;
  /** Echoed back unchanged, to prove data survives the round trip. */
  readonly payload: unknown;
}

export interface RunRequest {
  readonly type: "run";
  readonly id: number;
  readonly setup: RunSetup;
  /** Games `start` to `end - 1` of the run. */
  readonly start: number;
  readonly end: number;
  /** Which engine plays. Only "stub" exists until the kernel can play. */
  readonly engine: "stub";
}

export interface CancelRequest {
  readonly type: "cancel";
  readonly id: number;
  /** The run to stop. */
  readonly run: number;
}

export type Request = PingRequest | RunRequest | CancelRequest;

export interface PongReply {
  readonly type: "pong";
  readonly id: number;
  readonly payload: unknown;
  /** The engine version the worker actually loaded. */
  readonly engineVersion: string;
}

export interface ProgressReply {
  readonly type: "progress";
  readonly id: number;
  readonly done: number;
  readonly total: number;
}

export interface ResultReply {
  readonly type: "result";
  readonly id: number;
  readonly result: RunResult;
}

export interface ErrorReply {
  readonly type: "error";
  /** The request this answers, or -1 when the request was unreadable. */
  readonly id: number;
  readonly message: string;
}

export type Reply = PongReply | ProgressReply | ResultReply | ErrorReply;

export function isRequest(value: unknown): value is Request {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (!Number.isInteger(candidate.id)) return false;
  switch (candidate.type) {
    case "ping":
      return true;
    case "run":
      return (
        typeof candidate.setup === "object" && candidate.setup !== null &&
        Number.isInteger(candidate.start) && Number.isInteger(candidate.end) &&
        candidate.engine === "stub"
      );
    case "cancel":
      return Number.isInteger(candidate.run);
    default:
      return false;
  }
}
