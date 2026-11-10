import { ENGINE_VERSION } from "@goldfly/engine";
import { runGames, stubEngine, type RunSetup } from "@goldfly/engine/sim";
import { describe, expect, it } from "vitest";
import { EngineClient, type WorkerLike } from "../src/client";
import { WorkerPool } from "../src/pool";
import { WorkerHandler } from "../src/worker/handle";
import type { Reply } from "../src/worker/protocol";

/**
 * A worker in the same thread: messages go through structuredClone, the same
 * copy the browser makes, and arrive in a later task, like a real worker's.
 */
class FakeWorker implements WorkerLike {
  private onMessage: ((event: MessageEvent<unknown>) => void) | undefined;
  private onError: ((event: ErrorEvent) => void) | undefined;
  private readonly handler: WorkerHandler;
  terminated = false;

  constructor(answer?: (data: unknown) => Reply) {
    const deliver = (reply: Reply): void => {
      const copy = structuredClone(reply);
      setTimeout(() => this.onMessage?.({ data: copy } as MessageEvent<unknown>), 0);
    };
    this.handler = new WorkerHandler(deliver);
    if (answer !== undefined) {
      this.handler = { receive: async (data: unknown) => deliver(answer(data)) } as unknown as WorkerHandler;
    }
  }

  postMessage(message: unknown): void {
    const copy = structuredClone(message);
    setTimeout(() => void this.handler.receive(copy), 0);
  }

  addEventListener(type: "message" | "error", listener: (event: never) => void): void {
    if (type === "message") this.onMessage = listener as (event: MessageEvent<unknown>) => void;
    else this.onError = listener as (event: ErrorEvent) => void;
  }

  fail(message: string): void {
    this.onError?.({ message } as ErrorEvent);
  }

  terminate(): void {
    this.terminated = true;
  }
}

const doc = (id: string, name: string) =>
  ({ ir_version: 1, oracle_id: id, name, card_hash: "h", faces: [] }) as never;

const setup: RunSetup = {
  decks: [0, 1, 2, 3].map((seat) => ({
    commanders: [],
    cards: [
      { oracle_id: "land", count: 40 },
      { oracle_id: `spell${seat}`, count: 59 },
    ],
  })),
  cards: {
    land: doc("land", "Island"),
    spell0: doc("spell0", "Spell Zero"),
    spell1: doc("spell1", "Spell One"),
    spell2: doc("spell2", "Spell Two"),
    spell3: doc("spell3", "Spell Three"),
  },
  runSeed: 42,
  maxRounds: 25,
};

describe("worker message handling", () => {
  async function replies(message: unknown): Promise<Reply[]> {
    const out: Reply[] = [];
    await new WorkerHandler((r) => out.push(r)).receive(message);
    return out;
  }

  it("answers a ping with the engine version and the payload", async () => {
    expect(await replies({ type: "ping", id: 7, payload: { a: [1, 2] } })).toEqual([
      { type: "pong", id: 7, payload: { a: [1, 2] }, engineVersion: ENGINE_VERSION },
    ]);
  });

  it("rejects a message it does not understand, keeping the id", async () => {
    expect(await replies({ type: "dance", id: 3 })).toMatchObject([{ type: "error", id: 3 }]);
    expect(await replies("nonsense")).toMatchObject([{ type: "error", id: -1 }]);
    expect(await replies({ type: "run", id: 4, setup, start: 0, end: 2, engine: "real" }))
      .toMatchObject([{ type: "error", id: 4 }]);
  });

  it("runs a slice, reporting progress, then the result", async () => {
    const out = await replies({ type: "run", id: 9, setup, start: 0, end: 12, engine: "stub" });
    const progress = out.filter((r) => r.type === "progress").map((r) => (r as { done: number }).done);
    expect(progress).toEqual([5, 10, 12]);
    const last = out.at(-1);
    expect(last?.type).toBe("result");
    expect(last?.type === "result" && last.result).toEqual(await runGames(stubEngine, setup, 0, 12));
  });
});

describe("EngineClient", () => {
  it("round-trips a payload", async () => {
    const client = new EngineClient(new FakeWorker());
    const reply = await client.ping({ deck: "x", seeds: [1, 2, 3] });
    expect(reply.payload).toEqual({ deck: "x", seeds: [1, 2, 3] });
    expect(reply.engineVersion).toBe(ENGINE_VERSION);
  });

  it("matches concurrent replies to their requests", async () => {
    const client = new EngineClient(new FakeWorker());
    const replies = await Promise.all([1, 2, 3].map((n) => client.ping(n)));
    expect(replies.map((r) => r.payload)).toEqual([1, 2, 3]);
  });

  it("turns an error reply into a rejected promise", async () => {
    const client = new EngineClient(
      new FakeWorker((data) => ({ type: "error", id: (data as { id: number }).id, message: "boom" })),
    );
    await expect(client.ping()).rejects.toThrow("boom");
  });

  it("rejects everything in flight when the worker fails", async () => {
    const worker = new FakeWorker(() => ({ type: "pong", id: -999, payload: null, engineVersion: "" }));
    const client = new EngineClient(worker);
    const pending = client.ping();
    worker.fail("script error");
    await expect(pending).rejects.toThrow("script error");
  });

  it("rejects what is in flight when terminated", async () => {
    const worker = new FakeWorker();
    const client = new EngineClient(worker);
    const pending = client.ping();
    client.terminate();
    await expect(pending).rejects.toThrow("terminated");
    expect(worker.terminated).toBe(true);
  });

  it("runs games and reports progress", async () => {
    const client = new EngineClient(new FakeWorker());
    const seen: number[] = [];
    const result = await client.run(setup, 3, 10, (done) => seen.push(done)).result;
    expect(result.records.map((r) => r.index)).toEqual([3, 4, 5, 6, 7, 8, 9]);
    expect(seen.at(-1)).toBe(7);
  });

  it("stops a run when cancelled, and still answers", async () => {
    const client = new EngineClient(new FakeWorker());
    const handle = client.run(setup, 0, 10_000);
    client.cancel(handle.id);
    const result = await handle.result;
    expect(result.complete).toBe(false);
    expect(result.records.length).toBeLessThan(10_000);
  });
});

describe("WorkerPool", () => {
  const pool = (size: number) => new WorkerPool(size, () => new EngineClient(new FakeWorker()));

  it("gives the same games with one worker or several", async () => {
    const one = await pool(1).run(setup, 40).result;
    const three = await pool(3).run(setup, 40).result;
    expect(three).toEqual(one);
    expect(one.records.map((r) => r.index)).toEqual(Array.from({ length: 40 }, (_, i) => i));
    expect(one).toEqual(await runGames(stubEngine, setup, 0, 40));
  });

  it("reports overall progress up to the total", async () => {
    const seen: number[] = [];
    await pool(4).run(setup, 30, (done, total) => {
      expect(total).toBe(30);
      seen.push(done);
    }).result;
    expect(seen.at(-1)).toBe(30);
  });

  it("cancels every worker", async () => {
    const run = pool(3).run(setup, 30_000);
    run.cancel();
    const result = await run.result;
    expect(result.complete).toBe(false);
    expect(result.records.length).toBeLessThan(30_000);
  });

  it("uses no more workers than there are games", async () => {
    const result = await pool(8).run(setup, 3).result;
    expect(result.records.map((r) => r.index)).toEqual([0, 1, 2]);
  });
});
