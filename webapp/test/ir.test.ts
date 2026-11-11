import { describe, expect, it } from "vitest";
import { IrClient, type DeckManifest, type Fetch } from "../src/ir";

/** schema/samples: real exported cards, by oracle id, plus the bench decks. */
const files = import.meta.glob<Record<string, unknown>>("../../schema/samples/*.json", {
  eager: true,
  import: "default",
});
const samples = new Map<string, Record<string, unknown>>();
let decks: Record<string, { commanders: string[]; cards: { oracle_id: string; name: string; count: number }[] }> = {};
for (const [path, document] of Object.entries(files)) {
  if (path.endsWith("decks.json")) decks = document as typeof decks;
  else samples.set(String(document.oracle_id), document);
}

function manifestFor(deckName: string, build = "b1"): DeckManifest {
  const deck = decks[deckName];
  if (deck === undefined) throw new Error(`no sample deck ${deckName}`);
  const ids = [...deck.commanders, ...deck.cards.map((c) => c.oracle_id)];
  const cards: Record<string, { name: string; card_hash: string; url: string }> = {};
  for (const id of ids) {
    const document = samples.get(id);
    if (document === undefined) throw new Error(`no sample card ${id}`);
    const hash = String(document.card_hash);
    cards[id] = { name: String(document.name), card_hash: hash, url: `/ir/v1/${id}.json?h=${hash}&b=${build}` };
  }
  return {
    ir_version: 1,
    snapshot: "test",
    build,
    decks: [{ ...deck, unresolved: [], live_ir: [], issues: [] }],
    cards,
  };
}

/** A server made of the sample files, counting what it is asked for. */
function fakeServer(manifest: DeckManifest, tamper?: (doc: Record<string, unknown>) => Record<string, unknown>) {
  const calls: string[] = [];
  const fetch: Fetch = async (input) => {
    calls.push(input);
    if (input.endsWith("/api/ir/decks")) return Response.json(manifest);
    const id = /\/ir\/v1\/([0-9a-f-]+)\.json/.exec(input)?.[1];
    const document = id === undefined ? undefined : samples.get(id);
    if (document === undefined) return Response.json({ error: "no such card" }, { status: 404 });
    return Response.json(tamper ? tamper(structuredClone(document)) : document);
  };
  return { fetch, calls };
}

describe("IrClient", () => {
  it("loads every card of a deck, checked against the IR version", async () => {
    const manifest = manifestFor("aggro");
    const server = fakeServer(manifest);
    const result = await new IrClient({ fetch: server.fetch }).load(["deck text"]);

    expect(result.cards.size).toBe(Object.keys(manifest.cards).length);
    expect(result.fetched).toBe(result.cards.size);
    const commander = result.manifest.decks[0]?.commanders[0] ?? "";
    expect(result.cards.get(commander)?.faces[0]?.ir_version).toBe(1);
  });

  it("fetches nothing the second time for the same cards", async () => {
    const server = fakeServer(manifestFor("combo"));
    const client = new IrClient({ fetch: server.fetch });
    await client.load(["x"]);
    const before = server.calls.length;
    const again = await client.load(["x"]);
    expect(again.fetched).toBe(0);
    expect(server.calls.length).toBe(before + 1); // only the deck lookup
  });

  it("refetches a card whose hash changed", async () => {
    const manifest = manifestFor("stax");
    const client = new IrClient({ fetch: fakeServer(manifest).fetch });
    await client.load(["x"]);
    const [id, ref] = Object.entries(manifest.cards)[0] ?? [];
    if (id === undefined || ref === undefined) throw new Error("empty deck");
    const changed: DeckManifest = {
      ...manifest,
      cards: { ...manifest.cards, [id]: { ...ref, card_hash: "different" } },
    };
    const server = fakeServer(changed, (doc) => (doc.oracle_id === id ? { ...doc, card_hash: "different" } : doc));
    const result = await new IrClient({ fetch: server.fetch }).load(["x"]);
    expect(result.fetched).toBe(Object.keys(changed.cards).length);
  });

  it("refetches every card after a new build, even with the same card hashes", async () => {
    const server = fakeServer(manifestFor("aggro", "b1"));
    const client = new IrClient({ fetch: server.fetch });
    await client.load(["x"]);
    const rebuilt = fakeServer(manifestFor("aggro", "b2"));
    const again = await new IrClient({ fetch: rebuilt.fetch }).load(["x"]);
    expect(again.fetched).toBe(again.cards.size);
    // And within one client: same hashes, new build -> fetched again.
    const shared = new IrClient({ fetch: server.fetch });
    await shared.load(["x"]);
    const swap: Fetch = (input, init) => rebuilt.fetch(input, init);
    (shared as unknown as { fetch: Fetch }).fetch = swap;
    expect((await shared.load(["x"])).fetched).toBe(again.cards.size);
  });

  it("uses a live-parsed card inline, fetches nothing for it, and never holds it", async () => {
    const manifest = manifestFor("combo");
    const [id, ref] = Object.entries(manifest.cards)[0] ?? [];
    if (id === undefined || ref === undefined) throw new Error("empty deck");
    const inline: DeckManifest = {
      ...manifest,
      cards: { ...manifest.cards, [id]: { name: ref.name, card_hash: ref.card_hash, document: samples.get(id) } },
    };
    const server = fakeServer(inline);
    const client = new IrClient({ fetch: server.fetch });
    const first = await client.load(["x"]);
    expect(first.live).toBe(1);
    expect(first.cards.get(id)?.oracle_id).toBe(id);
    expect(server.calls.some((url) => url.includes(id))).toBe(false);
    // Next time the same card is exported: it must be fetched, not reused.
    const exported = fakeServer(manifest);
    (client as unknown as { fetch: Fetch }).fetch = exported.fetch;
    const second = await client.load(["x"]);
    expect(second.fetched).toBe(1);
    expect(second.live).toBe(0);
  });

  it("refuses a card file that does not match the manifest", async () => {
    const server = fakeServer(manifestFor("control"), (doc) => ({ ...doc, card_hash: "stale" }));
    await expect(new IrClient({ fetch: server.fetch }).load(["x"])).rejects.toThrow("does not match");
  });

  it("refuses a card file of another IR version", async () => {
    const server = fakeServer(manifestFor("control"), (doc) => ({ ...doc, ir_version: 2 }));
    await expect(new IrClient({ fetch: server.fetch }).load(["x"])).rejects.toThrow("IR version");
  });

  it("passes on the server's error", async () => {
    const fetch: Fetch = async () => Response.json({ error: "card IR has not been exported" }, { status: 503 });
    await expect(new IrClient({ fetch }).load(["x"])).rejects.toThrow("not been exported");
  });

  it("sends the decklists as JSON", async () => {
    let sent: RequestInit | undefined;
    const fetch: Fetch = async (_input, init) => {
      sent = init;
      return Response.json({ ir_version: 1, snapshot: "", build: "", decks: [], cards: {} });
    };
    await new IrClient({ fetch, base: "http://server" }).load(["1 Sol Ring"]);
    expect(sent?.method).toBe("POST");
    expect(JSON.parse(String(sent?.body))).toEqual({ decklists: ["1 Sol Ring"] });
  });
});
