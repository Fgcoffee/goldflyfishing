/**
 * The harness page, end to end: decklists in, card IR fetched from the
 * server, games played in Web Workers, a result table out. The real app's
 * Results page replaces the table once real games run; swapping the stub
 * for the real engine happens in the worker (`worker/handle.ts`), not here.
 */

import { summarize, type RunResult, type RunSetup } from "@goldfly/engine/sim";
import { EngineClient } from "./client";
import { IrClient, type DeckManifest } from "./ir";
import { WorkerPool, defaultWorkers } from "./pool";

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`missing ${selector}`);
  return element;
};

const status = $("#status");
const decksBox = $<HTMLTextAreaElement>("#decks");
const runButton = $<HTMLButtonElement>("#run");
const cancelButton = $<HTMLButtonElement>("#cancel");
const progress = $<HTMLProgressElement>("#progress");
const runStatus = $("#run-status");
const notes = $<HTMLUListElement>("#notes");
const table = $<HTMLTableElement>("#results");

const irClient = new IrClient();
const pool = new WorkerPool(defaultWorkers());

/** Prove every worker loaded the engine before anything else. */
async function checkWorkers(): Promise<void> {
  const client = new EngineClient();
  try {
    const reply = await client.ping();
    status.textContent = `${pool.size} worker(s) ready, engine ${reply.engineVersion}.`;
  } catch (error) {
    status.className = "bad";
    status.textContent = `The worker failed to start: ${String(error)}`;
  } finally {
    client.terminate();
  }
}

function numberOf(selector: string, fallback: number): number {
  const value = Number($<HTMLInputElement>(selector).value);
  return Number.isFinite(value) ? Math.floor(value) : fallback;
}

function note(text: string, bad = false): void {
  const item = document.createElement("li");
  item.textContent = text;
  if (bad) item.className = "bad";
  notes.append(item);
}

function seatName(manifest: DeckManifest, seat: number): string {
  const deck = manifest.decks[seat] ?? manifest.decks[0];
  const commander = deck?.commanders[0];
  const name = commander === undefined ? undefined : manifest.cards[commander]?.name;
  const label = seat === 0 ? "You" : `Opponent ${seat}`;
  return name === undefined ? label : `${label}: ${name}`;
}

const percent = (x: number): string => `${(x * 100).toFixed(1)}%`;
const turn = (x: number | null): string => (x === null ? "-" : x.toFixed(1));

function showTable(manifest: DeckManifest, result: RunResult): void {
  const summary = summarize(result.records, result.crashed);
  const body = table.tBodies[0] as HTMLTableSectionElement;
  body.replaceChildren(
    ...summary.seats.map((seat) => {
      const row = document.createElement("tr");
      const cells = [
        seatName(manifest, seat.seat),
        String(seat.wins),
        percent(seat.winRate),
        turn(seat.averageWinRound),
        `${seat.commanderLanded} of ${summary.games}`,
        turn(seat.averageCommanderRound),
        percent(seat.mulliganRate),
      ];
      row.replaceChildren(...cells.map((text) => Object.assign(document.createElement("td"), { textContent: text })));
      return row;
    }),
  );
  table.hidden = false;
  if (summary.stalls > 0) note(`${summary.stalls} game(s) hit the turn cap with no winner (${percent(summary.stallRate)}).`);
}

let cancel: (() => void) | null = null;

async function run(): Promise<void> {
  runButton.disabled = true;
  cancelButton.disabled = false;
  notes.replaceChildren();
  runStatus.className = "";
  table.hidden = true;
  progress.hidden = false;
  progress.value = 0;
  const games = Math.max(1, Math.min(100_000, numberOf("#games", 1000)));

  try {
    runStatus.textContent = "Looking up the cards...";
    const decklists = decksBox.value.split(/^---\s*$/m).map((d) => d.trim()).filter(Boolean).slice(0, 4);
    if (decklists.length === 0) throw new Error("paste a decklist first");
    const { manifest, cards, fetched, live } = await irClient.load(decklists);
    manifest.decks.forEach((deck, seat) => {
      for (const name of deck.unresolved) note(`${seatName(manifest, seat)}: not a card I know: ${name}`, true);
      for (const name of deck.live_ir) note(`${name} was parsed live by the server (not in the export).`);
    });
    const first = manifest.decks[0];
    if (first === undefined) throw new Error("no decks came back");

    const setup: RunSetup = {
      decks: Array.from({ length: 4 }, (_, seat) => manifest.decks[seat] ?? first),
      cards: Object.fromEntries(cards),
      runSeed: Math.max(0, numberOf("#seed", 0)) >>> 0,
      maxRounds: Math.max(4, numberOf("#rounds", 25)),
    };

    const started = performance.now();
    const running = pool.run(setup, games, (done) => {
      progress.value = done / games;
      runStatus.textContent = `Playing... ${done} of ${games} on ${pool.size} worker(s)`;
    });
    cancel = running.cancel;
    const result = await running.result;
    const seconds = (performance.now() - started) / 1000;

    runStatus.textContent =
      `${result.complete ? "" : "Cancelled: "}${result.records.length} game(s) in ${seconds.toFixed(2)} s ` +
      `on ${pool.size} worker(s). Cards: ${cards.size} (${fetched} downloaded` +
      `${live > 0 ? `, ${live} parsed live` : ""}).`;
    for (const crash of result.crashed.slice(0, 10)) {
      note(`Game ${crash.index} (seed ${crash.seed}) crashed and was left out: ${crash.error}`, true);
    }
    showTable(manifest, result);

    // Replay check: the first games again must give identical digests.
    if (result.complete && result.records.length > 0) {
      const sample = Math.min(100, result.records.length);
      const again = await pool.run(setup, sample).result;
      const same = again.records.every((r, i) => r.digest === result.records[i]?.digest);
      note(same ? `Replay check: the first ${sample} games replayed identically.`
                : `Replay check FAILED: a game did not replay identically from its seed.`, !same);
      document.body.dataset.run = same ? "ok" : "mismatch";
    }
  } catch (error) {
    runStatus.className = "bad";
    runStatus.textContent = String(error instanceof Error ? error.message : error);
    document.body.dataset.run = "error";
  } finally {
    cancel = null;
    runButton.disabled = false;
    cancelButton.disabled = true;
    progress.hidden = true;
  }
}

runButton.addEventListener("click", () => void run());
cancelButton.addEventListener("click", () => cancel?.());
void checkWorkers();
