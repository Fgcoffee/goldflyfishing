/// <reference lib="webworker" />
// The replay worker's entry point: messages in, `ReplayHandler` answers out.

import type { ReplayRequest } from "./replay_protocol";
import { ReplayHandler } from "./replay_worker_handle";

const handler = new ReplayHandler();
self.addEventListener("message", (event: MessageEvent<ReplayRequest>) => {
  self.postMessage(handler.handle(event.data));
});
