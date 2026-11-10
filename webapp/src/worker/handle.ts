/**
 * What a simulation worker does with a message, as a plain function.
 *
 * Kept apart from the worker entry point so it can be tested without a
 * browser: the entry point only wires this to `onmessage`.
 */

import { ENGINE_VERSION } from "@goldfly/engine";
import { isRequest, type Reply } from "./protocol";

export function handle(message: unknown): Reply {
  if (!isRequest(message)) {
    const id = idOf(message);
    return { type: "error", id, message: "not a request this worker understands" };
  }
  switch (message.type) {
    case "ping":
      return {
        type: "pong",
        id: message.id,
        payload: message.payload,
        engineVersion: ENGINE_VERSION,
      };
  }
}

function idOf(message: unknown): number {
  if (typeof message === "object" && message !== null) {
    const id = (message as { id?: unknown }).id;
    if (typeof id === "number" && Number.isInteger(id)) return id;
  }
  return -1;
}
