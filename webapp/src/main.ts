/**
 * The harness page: starts one worker per spare core and pings each, to show
 * the engine loads and answers off the main thread. The real app replaces
 * this once games can run.
 */

import { EngineClient } from "./client";

const out = document.querySelector<HTMLElement>("#status");
const list = document.querySelector<HTMLOListElement>("#workers");

async function main(): Promise<void> {
  if (out === null || list === null) return;
  const cores = Math.max(1, (navigator.hardwareConcurrency || 2) - 1);
  out.textContent = `Starting ${cores} worker(s)...`;

  const started = performance.now();
  const results = await Promise.allSettled(
    Array.from({ length: cores }, async (_, index) => {
      const client = new EngineClient();
      try {
        const reply = await client.ping({ worker: index, sentAt: Date.now() });
        return { index, reply, ms: performance.now() - started };
      } finally {
        client.terminate();
      }
    }),
  );

  let ok = 0;
  for (const result of results) {
    const item = document.createElement("li");
    if (result.status === "fulfilled") {
      ok += 1;
      const { index, reply, ms } = result.value;
      item.textContent = `worker ${index}: engine ${reply.engineVersion}, echoed ${JSON.stringify(reply.payload)} in ${ms.toFixed(0)} ms`;
    } else {
      item.textContent = `failed: ${String(result.reason)}`;
      item.className = "bad";
    }
    list.append(item);
  }
  out.textContent = `${ok} of ${cores} worker(s) answered.`;
  document.body.dataset.ready = ok === cores ? "ok" : "failed";
}

void main();
