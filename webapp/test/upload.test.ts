import { runGames, stubEngine, type RunSetup } from "@goldfly/engine/sim";
import { describe, expect, it } from "vitest";
import type { DeckManifest, Fetch } from "../src/ir";
import { UploadRefused, runUpload, uploadRun } from "../src/upload";

const doc = (id: string, name: string) =>
  ({ ir_version: 2, oracle_id: id, name, card_hash: `hash-${id}`, faces: [] }) as never;

const setup: RunSetup = {
  decks: [0, 1].map(() => ({ commanders: ["cmd"], cards: [{ oracle_id: "land", count: 99 }] })),
  cards: { land: doc("land", "Island"), cmd: doc("cmd", "Krenko") },
  runSeed: 9,
  maxRounds: 25,
};

const manifest: DeckManifest = {
  ir_version: 2,
  snapshot: "snap",
  build: "irbuild",
  decks: [],
  cards: {
    land: { name: "Island", card_hash: "hash-land", url: "/ir/v2/land.json" },
    cmd: { name: "Krenko", card_hash: "hash-cmd", url: "/ir/v2/cmd.json" },
  },
};

describe("run upload", () => {
  it("carries what the server needs to replay any game", async () => {
    const result = await runGames(stubEngine, setup, 0, 5);
    const body = runUpload(manifest, setup, result, { start: 0, end: 5 });
    expect(body).toMatchObject({
      run_seed: 9, game_start: 0, game_end: 5, hero: 0, max_rounds: 25, complete: true,
      ir_build: "irbuild", snapshot: "snap",
    });
    expect(typeof body.engine_build).toBe("string");
    expect((body.decks as { cards: unknown[] }[])[0]?.cards).toEqual([
      { oracle_id: "land", card_hash: "hash-land", count: 99 },
    ]);
    expect((body.records as unknown[]).length).toBe(5);
  });

  it("sends it gzipped as JSON and returns the server's answer", async () => {
    let sent: { url: string; init?: RequestInit } | undefined;
    const fetch: Fetch = async (url, init) => {
      sent = { url, init };
      return Response.json({ id: "r1", games: 5, crashed: 0, spot_check: "pending", signed_in: false }, { status: 201 });
    };
    const stored = await uploadRun({ hello: "world" }, { fetch });
    expect(stored.id).toBe("r1");
    expect(sent?.url).toBe("/api/browser-runs");
    const headers = sent?.init?.headers as Record<string, string>;
    expect(headers["content-encoding"]).toBe("gzip");
    const unzipped = await new Response(
      (sent?.init?.body as Blob).stream().pipeThrough(new DecompressionStream("gzip")),
    ).text();
    expect(JSON.parse(unzipped)).toEqual({ hello: "world" });
  });

  it("passes on a refusal", async () => {
    const fetch: Fetch = async () => Response.json({ error: "game 3 does not have its seed" }, { status: 400 });
    await expect(uploadRun({}, { fetch })).rejects.toMatchObject({ message: "game 3 does not have its seed", reload: false });
  });

  it("says when the page must reload for a newer engine", async () => {
    const fetch: Fetch = async () =>
      Response.json({ error: "the engine has been updated", reload: true, engine_build: "new" }, { status: 409 });
    const refusal = await uploadRun({}, { fetch }).catch((error: unknown) => error);
    expect(refusal).toBeInstanceOf(UploadRefused);
    expect((refusal as UploadRefused).reload).toBe(true);
  });
});
