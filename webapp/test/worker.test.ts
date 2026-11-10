import { ENGINE_VERSION } from "@goldfly/engine";
import { describe, expect, it } from "vitest";
import { EngineClient, type WorkerLike } from "../src/client";
import { handle } from "../src/worker/handle";

/**
 * A worker that answers in a later task, like a real one, and sends what it
 * receives through structuredClone, the same copy the browser makes.
 */
class FakeWorker implements WorkerLike {
  private onMessage: ((event: MessageEvent<unknown>) => void) | undefined;
  private onError: ((event: ErrorEvent) => void) | undefined;
  terminated = false;

  constructor(private readonly answer: (data: unknown) => unknown = handle) {}

  postMessage(message: unknown): void {
    const copy = structuredClone(message);
    setTimeout(() => {
      const reply = this.answer(copy);
      this.onMessage?.({ data: structuredClone(reply) } as MessageEvent<unknown>);
    }, 0);
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

describe("worker message handling", () => {
  it("answers a ping with the engine version and the payload", () => {
    expect(handle({ type: "ping", id: 7, payload: { a: [1, 2] } })).toEqual({
      type: "pong",
      id: 7,
      payload: { a: [1, 2] },
      engineVersion: ENGINE_VERSION,
    });
  });

  it("rejects a message it does not understand, keeping the id", () => {
    expect(handle({ type: "dance", id: 3 })).toMatchObject({ type: "error", id: 3 });
    expect(handle("nonsense")).toMatchObject({ type: "error", id: -1 });
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
});
