import { runGames, seedFor, stubEngine, type RunSetup } from "@goldfly/engine/sim";
import { describe, expect, it } from "vitest";
import { EngineClient, type WorkerLike } from "../src/client";
import { WorkerPool } from "../src/pool";
import { WorkerHandler } from "../src/worker/handle";
import type { Reply } from "../src/worker/protocol";

/**
 * A worker in this thread, with two ways to go wrong:
 * - `silentFrom`: from that game index on it says nothing, as a worker
 *   stuck in a game that never ends does;
 * - `failAtLoad`: it fires `error` straight away, as a worker whose script
 *   won't load does.
 */
class FlakyWorker implements WorkerLike {
  private onMessage: ((event: MessageEvent<unknown>) => void) | undefined;
  private onError: ((event: ErrorEvent) => void) | undefined;
  private readonly handler: WorkerHandler;
  terminated = false;

  constructor(options: { silentFrom?: number; failAtLoad?: boolean } = {}) {
    let silent = false;
    this.handler = new WorkerHandler((reply: Reply) => {
      if (this.terminated) return;
      if (reply.type === "progress" && options.silentFrom !== undefined) {
        const index = reply.game?.record?.index ?? reply.game?.crash?.index ?? -1;
        if (index + 1 >= options.silentFrom) silent = true; // the next game hangs
        if (index >= options.silentFrom) return;
      }
      if (silent && reply.type !== "progress") return;
      const copy = structuredClone(reply);
      setTimeout(() => this.onMessage?.({ data: copy } as MessageEvent<unknown>), 0);
    });
    if (options.failAtLoad) setTimeout(() => this.onError?.({ message: "SyntaxError: bad script" } as ErrorEvent), 0);
  }

  postMessage(message: unknown): void {
    if (this.terminated) return;
    const copy = structuredClone(message);
    setTimeout(() => void this.handler.receive(copy), 0);
  }

  addEventListener(type: string, listener: (event: never) => void): void {
    if (type === "message") this.onMessage = listener as (event: MessageEvent<unknown>) => void;
    else if (type === "error") this.onError = listener as (event: ErrorEvent) => void;
  }

  terminate(): void {
    this.terminated = true;
  }
}

const doc = (id: string, name: string) =>
  ({ ir_version: 2, oracle_id: id, name, card_hash: "h", faces: [] }) as never;

const setup: RunSetup = {
  decks: [0, 1].map((seat) => ({ commanders: [], cards: [{ oracle_id: "land", count: 40 }, { oracle_id: `s${seat}`, count: 59 }] })),
  cards: { land: doc("land", "Island"), s0: doc("s0", "A"), s1: doc("s1", "B") },
  runSeed: 11,
  maxRounds: 25,
};

describe("a run always finishes", () => {
  it("times out a game that never ends, records it, and plays the rest on a fresh worker", async () => {
    let made = 0;
    const logs: string[] = [];
    const pool = new WorkerPool(1, () => new EngineClient(new FlakyWorker(made++ === 0 ? { silentFrom: 4 } : {})));
    const seen: number[] = [];
    const result = await pool.run(setup, 10, (done) => seen.push(done), "stub", {
      gameTimeoutMs: 100, engineBuild: "b1", log: (m) => logs.push(m),
    }).result;

    expect(result.complete).toBe(true);
    expect(result.records.map((r) => r.index)).toEqual([0, 1, 2, 3, 5, 6, 7, 8, 9]);
    expect(result.crashed).toEqual([
      { index: 4, seed: seedFor(11, 4), error: "timed out after 0.1 s", detail: "engine build b1" },
    ]);
    // The games that did finish are the games a clean run plays.
    const clean = await runGames(stubEngine, setup, 0, 10);
    expect(result.records).toEqual(clean.records.filter((r) => r.index !== 4));
    expect(seen.at(-1)).toBe(10);
    expect(made).toBe(2);
    expect(logs[0]).toContain("game 4");
  });

  it("replaces a worker that fails to load, recording the game it was given", async () => {
    let made = 0;
    const pool = new WorkerPool(1, () => new EngineClient(new FlakyWorker({ failAtLoad: made++ === 0 })));
    const result = await pool.run(setup, 5, undefined, "stub", { gameTimeoutMs: 1000 }).result;
    expect(result.complete).toBe(true);
    expect(result.crashed.map((c) => c.index)).toEqual([0]);
    expect(result.crashed[0]?.error).toContain("worker died: SyntaxError: bad script");
    expect(result.records.map((r) => r.index)).toEqual([1, 2, 3, 4]);
  });

  it("gives up a range when fresh workers keep failing, instead of restarting for ever", async () => {
    let made = 0;
    const pool = new WorkerPool(1, () => {
      made += 1;
      return new EngineClient(new FlakyWorker({ failAtLoad: true }));
    });
    const result = await pool.run(setup, 6, undefined, "stub", { gameTimeoutMs: 1000, maxRestarts: 2 }).result;
    expect(result.complete).toBe(true);
    expect(result.records).toHaveLength(0);
    expect(result.crashed.map((c) => c.index)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(result.crashed.at(-1)?.error).toContain("kept failing to start");
    expect(made).toBeLessThanOrEqual(4);
  });

  it("Stop ends a run whose worker is stuck mid-game, keeping what finished", async () => {
    const pool = new WorkerPool(1, () => new EngineClient(new FlakyWorker({ silentFrom: 3 })));
    const run = pool.run(setup, 10, undefined, "stub", { gameTimeoutMs: 60_000, stopGraceMs: 50 });
    // Let games 0-2 finish and game 3 "hang", then press Stop.
    await new Promise((resolve) => setTimeout(resolve, 100));
    const started = Date.now();
    run.cancel();
    const result = await run.result;
    expect(Date.now() - started).toBeLessThan(1000); // not the 60 s watchdog
    expect(result.complete).toBe(false);
    expect(result.records.map((r) => r.index)).toEqual([0, 1, 2]);
    expect(result.crashed).toEqual([{ index: 3, seed: seedFor(11, 3), error: "stopped mid-game", detail: "" }]);
  });

  it("Stop on a healthy run lets each worker finish its game and answer", async () => {
    const pool = new WorkerPool(2, () => new EngineClient(new FlakyWorker()));
    const run = pool.run(setup, 20_000, undefined, "stub", { stopGraceMs: 5_000 });
    await new Promise((resolve) => setTimeout(resolve, 30));
    run.cancel();
    const result = await run.result;
    expect(result.complete).toBe(false);
    expect(result.crashed).toHaveLength(0);
    expect(result.records.length).toBeGreaterThan(0);
  });

  it("keeps the other workers' games when one hangs", async () => {
    let made = 0;
    const pool = new WorkerPool(3, () => new EngineClient(new FlakyWorker(made++ === 1 ? { silentFrom: 5 } : {})));
    const result = await pool.run(setup, 12, undefined, "stub", { gameTimeoutMs: 50 }).result;
    expect(result.records.length + result.crashed.length).toBe(12);
    expect(result.crashed.map((c) => c.index)).toEqual([5]);
  });
});
