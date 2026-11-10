/// <reference lib="webworker" />
/**
 * The simulation worker's entry point. Each worker is one thread; the page
 * starts as many as the machine has cores to spare.
 */

import { handle } from "./handle";
import type { Reply } from "./protocol";

const scope = self as unknown as DedicatedWorkerGlobalScope;

scope.addEventListener("message", (event: MessageEvent<unknown>) => {
  let reply: Reply;
  try {
    reply = handle(event.data);
  } catch (error) {
    // A crash in the engine must come back as an answer, not a silent worker:
    // the page is waiting on a promise for this id.
    const id = (event.data as { id?: unknown } | null)?.id;
    reply = {
      type: "error",
      id: typeof id === "number" ? id : -1,
      message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    };
  }
  scope.postMessage(reply);
});
