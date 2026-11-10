/**
 * The messages between the page and a simulation worker.
 *
 * Every request carries an `id` and every reply echoes it, so the page can
 * have several requests in flight and match each reply to its promise. Only
 * plain data crosses the boundary (structured clone), never class instances.
 *
 * For now there is one request, `ping`, which proves the worker loaded the
 * engine. `run` (play N games from a seed and report) is added once the
 * engine's kernel and runner exist.
 */

export interface PingRequest {
  readonly type: "ping";
  readonly id: number;
  /** Echoed back unchanged, to prove data survives the round trip. */
  readonly payload: unknown;
}

export type Request = PingRequest;

export interface PongReply {
  readonly type: "pong";
  readonly id: number;
  readonly payload: unknown;
  /** The engine version the worker actually loaded. */
  readonly engineVersion: string;
}

export interface ErrorReply {
  readonly type: "error";
  /** The request this answers, or -1 when the request was unreadable. */
  readonly id: number;
  readonly message: string;
}

export type Reply = PongReply | ErrorReply;

export function isRequest(value: unknown): value is Request {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { type?: unknown; id?: unknown };
  return candidate.type === "ping" && Number.isInteger(candidate.id);
}
