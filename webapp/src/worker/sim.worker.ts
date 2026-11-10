/// <reference lib="webworker" />
/**
 * The simulation worker's entry point. Each worker is one thread; the page
 * starts as many as the machine has cores to spare.
 */

import { WorkerHandler } from "./handle";
import type { Reply } from "./protocol";

const scope = self as unknown as DedicatedWorkerGlobalScope;
const handler = new WorkerHandler((reply: Reply) => scope.postMessage(reply));

scope.addEventListener("message", (event: MessageEvent<unknown>) => {
  // Not awaited: a cancel has to be read while a run is still going.
  handler.receive(event.data).catch((error: unknown) => {
    // A failure outside a run's own error handling still answers, so the
    // page is never left waiting on a promise for this id.
    const id = (event.data as { id?: unknown } | null)?.id;
    scope.postMessage({
      type: "error",
      id: typeof id === "number" ? id : -1,
      message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    } satisfies Reply);
  });
});
