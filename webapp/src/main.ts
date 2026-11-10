/**
 * The harness page: starts one worker per spare core and pings each, to show
 * the engine loads and answers off the main thread. The real app replaces
 * this once games can run.
 */

import { EngineClient } from "./client";
import type { RunSetup } from "@goldfly/engine/sim";
import { IrClient } from "./ir";
import { WorkerPool } from "./pool";

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

// -- card IR ------------------------------------------------------------------

const irClient = new IrClient();
const irStatus = document.querySelector<HTMLElement>("#ir-status");
const irCards = document.querySelector<HTMLUListElement>("#ir-cards");
const decksBox = document.querySelector<HTMLTextAreaElement>("#decks");

async function loadIr(): Promise<void> {
  if (irStatus === null || irCards === null || decksBox === null) return;
  const decklists = decksBox.value.split(/^---\s*$/m).map((d) => d.trim()).filter(Boolean);
  irStatus.className = "";
  irStatus.textContent = "Loading...";
  irCards.replaceChildren();
  const started = performance.now();
  try {
    const { manifest, cards, fetched } = await irClient.load(decklists);
    const ms = performance.now() - started;
    const problems = manifest.decks.flatMap((d) => [
      ...d.unresolved.map((n) => `not a card: ${n}`),
      ...d.missing_ir.map((n) => `not in the IR export: ${n}`),
    ]);
    irStatus.textContent =
      `${cards.size} card(s) in ${manifest.decks.length} deck(s), ${fetched} downloaded, ` +
      `in ${ms.toFixed(0)} ms. IR v${manifest.ir_version}, snapshot ${manifest.snapshot.slice(0, 12)}.`;
    for (const problem of problems) {
      const item = document.createElement("li");
      item.className = "bad";
      item.textContent = problem;
      irCards.append(item);
    }
    for (const card of cards.values()) {
      const item = document.createElement("li");
      const abilities = card.faces.reduce((n, f) => n + f.abilities.length, 0);
      const partly = card.faces.some((f) => !f.fully_read);
      item.textContent = `${card.name}: ${card.faces.length} face(s), ${abilities} ability(ies)${partly ? ", partly read" : ""}`;
      irCards.append(item);
    }
  } catch (error) {
    irStatus.className = "bad";
    irStatus.textContent = String(error);
  }
}

document.querySelector("#load")?.addEventListener("click", () => void loadIr());

// -- a stub run ---------------------------------------------------------------

const runStatus = document.querySelector<HTMLElement>("#run-status");

/** Load the decks' IR, play 1000 stub games across the workers, twice. */
async function stubRun(): Promise<void> {
  if (runStatus === null || decksBox === null) return;
  runStatus.className = "";
  const decklists = decksBox.value.split(/^---\s*$/m).map((d) => d.trim()).filter(Boolean);
  const pool = new WorkerPool();
  try {
    runStatus.textContent = "Loading card IR...";
    const { manifest, cards } = await irClient.load(decklists);
    const setup: RunSetup = {
      // Seat 0 is the hero; missing seats are copies of the hero's deck.
      decks: Array.from({ length: 4 }, (_, seat) => manifest.decks[seat] ?? manifest.decks[0]!),
      cards: Object.fromEntries(cards),
      runSeed: 0,
      maxRounds: 25,
    };
    const games = 1000;
    const started = performance.now();
    const first = await pool.run(setup, games, (done) => {
      runStatus.textContent = `Playing... ${done}/${games} on ${pool.size} worker(s)`;
    }).result;
    const ms = performance.now() - started;
    const second = await pool.run(setup, games).result;
    const same = first.records.every((r, i) => r.digest === second.records[i]?.digest);
    const wins = first.records.filter((r) => r.winner === 0).length;
    runStatus.textContent =
      `${first.records.length} games (${first.crashed.length} crashed) in ${ms.toFixed(0)} ms ` +
      `on ${pool.size} worker(s). Seat 0 won ${wins}. ` +
      `Replay check: ${same ? "every game identical" : "MISMATCH"}.`;
    if (!same) runStatus.className = "bad";
    document.body.dataset.run = same ? "ok" : "mismatch";
  } catch (error) {
    runStatus.className = "bad";
    runStatus.textContent = String(error);
  } finally {
    pool.terminate();
  }
}

document.querySelector("#run")?.addEventListener("click", () => void stubRun());
