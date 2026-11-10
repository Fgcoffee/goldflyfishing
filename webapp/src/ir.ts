/**
 * Getting the card IR a run needs: the decks resolved by the server, then
 * only those cards' files.
 *
 * The server's card URLs carry the card's hash and are served immutable, so
 * the browser's HTTP cache already keeps a card until its text changes. On
 * top of that, cards fetched in this page are held in memory by oracle id
 * and hash, so a second run with the same decks fetches nothing.
 */

import { readCard, type CardDocument } from "@goldfly/engine/ir";

export interface DeckCard {
  readonly oracle_id: string;
  readonly name: string;
  readonly count: number;
}

export interface ResolvedDeck {
  readonly commanders: readonly string[];
  readonly cards: readonly DeckCard[];
  /** Names the card database didn't recognise. */
  readonly unresolved: readonly string[];
  /** Cards it recognised that the IR export doesn't have (stale export). */
  readonly missing_ir: readonly string[];
  readonly issues: readonly string[];
}

export interface CardRef {
  readonly name: string;
  readonly card_hash: string;
  readonly url: string;
}

export interface DeckManifest {
  readonly ir_version: number;
  readonly snapshot: string;
  readonly decks: readonly ResolvedDeck[];
  readonly cards: Readonly<Record<string, CardRef>>;
}

export interface DeckIr {
  readonly manifest: DeckManifest;
  /** Every card in the decks, by oracle id. */
  readonly cards: ReadonlyMap<string, CardDocument>;
  /** How many card files this call actually downloaded. */
  readonly fetched: number;
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export interface IrClientOptions {
  /** Prefix for server paths; "" for the same origin. */
  readonly base?: string;
  readonly fetch?: Fetch;
  /** Card files downloaded at once. */
  readonly concurrency?: number;
}

export class IrClient {
  private readonly base: string;
  private readonly fetch: Fetch;
  private readonly concurrency: number;
  /** oracle id -> [card_hash, document] */
  private readonly held = new Map<string, readonly [string, CardDocument]>();

  constructor(options: IrClientOptions = {}) {
    this.base = options.base ?? "";
    this.fetch = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.concurrency = Math.max(1, options.concurrency ?? 8);
  }

  /** Resolve decklists on the server and return every card's IR. */
  async load(decklists: readonly string[]): Promise<DeckIr> {
    const manifest = await this.resolve(decklists);
    const wanted = Object.entries(manifest.cards).filter(
      ([oracleId, ref]) => this.held.get(oracleId)?.[0] !== ref.card_hash,
    );

    let next = 0;
    const worker = async (): Promise<void> => {
      while (next < wanted.length) {
        const entry = wanted[next++];
        if (entry === undefined) return;
        const [oracleId, ref] = entry;
        const document = await this.card(ref);
        if (document.oracle_id !== oracleId || document.card_hash !== ref.card_hash) {
          throw new Error(`card file for ${ref.name} does not match the deck manifest`);
        }
        this.held.set(oracleId, [ref.card_hash, document]);
      }
    };
    await Promise.all(Array.from({ length: Math.min(this.concurrency, wanted.length) }, worker));

    const cards = new Map<string, CardDocument>();
    for (const oracleId of Object.keys(manifest.cards)) {
      const held = this.held.get(oracleId);
      if (held !== undefined) cards.set(oracleId, held[1]);
    }
    return { manifest, cards, fetched: wanted.length };
  }

  private async resolve(decklists: readonly string[]): Promise<DeckManifest> {
    const response = await this.fetch(`${this.base}/api/ir/decks`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decklists }),
      credentials: "same-origin",
    });
    const body = (await response.json()) as DeckManifest | { error?: string };
    if (!response.ok) {
      throw new Error(("error" in body && body.error) || `deck lookup failed (${response.status})`);
    }
    return body as DeckManifest;
  }

  private async card(ref: CardRef): Promise<CardDocument> {
    const response = await this.fetch(`${this.base}${ref.url}`);
    if (!response.ok) throw new Error(`could not fetch ${ref.name} (${response.status})`);
    // readCard refuses any other IR version, so an old cached file can never
    // be read as the current format.
    return readCard(await response.json());
  }
}
