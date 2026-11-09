"use strict";

/* The Recs tab: cards worth trying in the active deck, and a queue of them to
 * test.
 *
 * Recommendations come from the bridge (data/recs.py). A card goes straight
 * into the deck while the deck has room; once it is full, cards wait in the
 * queue, and testing the queue plays them against the deck's weakest cards to
 * see which they should replace - the cut lab's elimination, aimed at the
 * cards that might make way.
 */

const Recs = {
  reply: null,      // the last recommendations reply
  mode: "edhrec",
  queue: [],        // card names waiting to be tested
  loadedFor: null,  // the deck text the shown cards were found for
};

const DECK_LIMIT = 100;

/* ---------------------------------------------------------------- queue */

function recsLoadQueue() {
  try {
    const saved = JSON.parse(localStorage.getItem("mtgfish.recsQueue") || "[]");
    if (Array.isArray(saved)) Recs.queue = saved.filter((n) => typeof n === "string");
  } catch (err) { Recs.queue = []; }
}

function recsSaveQueue() {
  try { localStorage.setItem("mtgfish.recsQueue", JSON.stringify(Recs.queue)); } catch (err) { /* */ }
}

function recsQueueChanged() {
  recsSaveQueue();
  const list = document.getElementById("rc-qlist");
  list.textContent = "";
  if (!Recs.queue.length) {
    list.appendChild(el("li", "dim", "Nothing queued yet."));
  }
  Recs.queue.forEach((name) => {
    const item = el("li");
    item.appendChild(el("span", null, name));
    const remove = el("button", "ghost small", "×");
    remove.title = "take it out of the queue";
    remove.addEventListener("click", () => {
      Recs.queue = Recs.queue.filter((n) => n !== name);
      recsQueueChanged();
      recsRender();
    });
    item.appendChild(remove);
    list.appendChild(item);
  });
  document.getElementById("rc-qcount").textContent = Recs.queue.length ? `(${Recs.queue.length})` : "";
  document.getElementById("rc-test").disabled = !Recs.queue.length || CutLab.owner !== null;
  recsMeters();
}

function recsQueue(name) {
  if (Recs.queue.includes(name)) { toast(`${name} is already queued`); return; }
  Recs.queue.push(name);
  recsQueueChanged();
  recsRender();
  toast(`Queued ${name}`);
}

/* The number of cards in the active deck, commanders included. */
function recsDeckSize() {
  if (typeof Deck !== "undefined" && Deck.view && Deck.text === deckText()) {
    const cards = Deck.view.cards.reduce((sum, c) => sum + c.quantity, 0);
    return cards + Deck.view.commanders.length;
  }
  return Recs.reply ? Recs.reply.deck_size : 0;
}

/* Into the deck while there is room; into the queue once it is full. */
async function recsAdd(row) {
  if (!deckText()) { toast("Load a deck on the Decks screen first"); return; }
  if (typeof Deck !== "undefined" && (!Deck.view || Deck.text !== deckText())) {
    await loadDeckView(true);
  }
  if (recsDeckSize() >= DECK_LIMIT || !row.testable) {
    recsQueue(row.name);
    if (row.testable) toast(`Your deck is full - ${row.name} is queued to test against your weakest cards`);
    return;
  }
  addCard(row.name);
  if (Recs.reply) Recs.reply.deck_size += 1;
  row.in_deck = true;
  recsRender();
}

/* ---------------------------------------------------------------- meter */

function recsMeters() {
  const x = Number(document.getElementById("rc-x").value) || 0;
  const y = Number(document.getElementById("rc-y").value) || 0;
  const queued = Math.max(1, Recs.queue.length);
  const weakest = Math.max(2 * queued, queued + 4);
  // Phase 1 is the deck as it is: every card in every game.
  setMeter("rc-xmeter", "rc-xlabel", x, "Ranking your deck");
  setMeter("rc-ymeter", "rc-ylabel", y * 0.9 * weakest / (weakest + queued), "Head to head");
  setEta("rc-eta", x + y);
}

/* --------------------------------------------------------------- search */

function recsOptions() {
  const monthsChoice = document.getElementById("rc-months").value;
  const months = monthsChoice === "custom"
    ? Number(document.getElementById("rc-months-custom").value) || 0
    : Number(monthsChoice) || 0;
  return {
    decklist: deckText(),
    mode: Recs.mode,
    goal: document.getElementById("rc-goal").value,
    months,
    sort: document.getElementById("rc-sort").value,
    lands: document.getElementById("rc-lands").checked,
    limit: 60,
  };
}

async function recsFind() {
  const host = document.getElementById("rc-cards");
  const notes = document.getElementById("rc-notes");
  const options = recsOptions();
  if (!options.decklist) {
    host.textContent = "";
    host.appendChild(recsEmpty("No active deck",
      "Paste a decklist or an Archidekt URL on the Decks screen, then come back here."));
    return;
  }
  const button = document.getElementById("rc-go");
  button.disabled = true;
  notes.textContent = "";
  host.textContent = "";
  host.appendChild(el("div", "hint", options.mode === "edhrec" || options.months
    ? "Asking EDHREC and Scryfall..." : "Matching your deck's tags..."));
  setStatus("finding recommendations...");
  const reply = await call("recommendations", JSON.stringify(options));
  button.disabled = false;
  if (reply.error) { host.textContent = ""; return; }
  Recs.reply = reply;
  Recs.loadedFor = options.decklist;
  setStatus(`${reply.cards.length} recommendations`);

  const goal = document.getElementById("rc-goal");
  if (goal.options.length <= 1 && reply.goals) {
    Object.entries(reply.goals).forEach(([value, label]) => {
      const option = el("option", null, label);
      option.value = value;
      goal.appendChild(option);
    });
    goal.value = options.goal;
  }
  const line = document.getElementById("rc-deckline");
  line.textContent = reply.commanders.length
    ? `For ${reply.commanders.join(" + ")} (${reply.identity || "colourless"}) - `
      + `your deck has ${reply.deck_size} cards. Only Commander-legal cards in these colours `
      + "are shown, plus cards not released yet."
    : "Your deck has no commander, so colours come from its cards and EDHREC has nothing to say.";
  (reply.notes || []).forEach((note) => notes.appendChild(el("div", "issue warning", note)));
  recsRender();
}

function recsEmpty(title, text) {
  const box = el("div", "empty");
  box.appendChild(el("h2", null, title));
  box.appendChild(el("p", null, text));
  return box;
}

/* ---------------------------------------------------------------- tiles */

function recsRender() {
  const host = document.getElementById("rc-cards");
  host.textContent = "";
  const reply = Recs.reply;
  if (!reply) return;
  if (!reply.cards.length) {
    host.appendChild(recsEmpty("Nothing matched",
      "Try another goal, a wider release window, or the other source."));
    return;
  }
  if (Recs.mode === "similar" && reply.cards.some((c) => c.on_edhrec)) {
    host.appendChild(el("p", "hint small",
      "Cards outlined in gold are also on your commander's EDHREC page."));
  }
  const grid = el("div", "cardgrid recgrid");
  reply.cards.forEach((row) => grid.appendChild(recTile(row)));
  host.appendChild(grid);
}

function recTile(row) {
  const wrap = el("div", "rec");
  const highlight = Recs.mode === "similar" && row.on_edhrec;
  const tile = el("div", `tile ${row.status}${highlight ? " on-edhrec" : ""}`);
  tile.tabIndex = 0;
  tile.title = row.name;

  if (row.images && row.images.normal) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = row.name;
    img.src = row.images.normal;
    tile.appendChild(img);
  } else {
    const box = el("div", "fallback");
    box.appendChild(el("strong", null, row.name));
    if (row.type_line) box.appendChild(el("span", "dim", row.type_line));
    tile.appendChild(box);
  }

  // Parse status, exactly as the Deck tab shows it.
  if (row.status !== "ok") {
    const text = row.status === "partial"
      ? `Partly read - ${row.understood}/${row.abilities}`
      : row.missing ? "Not in card database yet" : STATUS_TEXT[row.status];
    tile.appendChild(el("span", "badge", text));
  }
  if (row.upcoming) tile.appendChild(el("span", "qty upcoming", "Upcoming"));

  const queued = Recs.queue.includes(row.name);
  const actions = el("div", "tile-actions");
  const action = (label, title, fn) => {
    const button = el("button", null, label);
    button.title = title;
    button.addEventListener("click", (event) => { event.stopPropagation(); fn(); });
    actions.appendChild(button);
  };
  if (!queued && !row.in_deck) {
    if (row.testable) action("+", "add to the deck (or the queue if the deck is full)", () => recsAdd(row));
    action("⇢", "add to the test queue", () => recsQueue(row.name));
  }
  tile.appendChild(actions);

  row.drawerActions = (button) => {
    if (row.in_deck || Recs.queue.includes(row.name)) return;
    if (row.testable) button("Add to deck", () => { closeDrawer(); recsAdd(row); }, "primary");
    button("Queue to test", () => { closeDrawer(); recsQueue(row.name); }, "ghost");
  };
  tile.addEventListener("click", () => {
    if (row.testable && typeof openDrawer === "function") openDrawer(row);
    else if (row.scryfall_uri) window.open(row.scryfall_uri, "_blank", "noopener");
  });
  wrap.appendChild(tile);

  const meta = el("div", "rec-meta");
  const chips = el("div", "rec-chips");
  if (queued) chips.appendChild(el("span", "pill partial", "Queued"));
  if (row.in_deck) chips.appendChild(el("span", "pill ok", "Added"));
  if (highlight) chips.appendChild(el("span", "pill edhrec", "On EDHREC"));
  if (row.inclusion) chips.appendChild(el("span", "pill off", `${Math.round(row.inclusion * 100)}% of decks`));
  if (row.edhrec_rank) chips.appendChild(el("span", "pill off", `#${row.edhrec_rank}`));
  if (row.released_at) chips.appendChild(el("span", "pill off", row.released_at));
  meta.appendChild(chips);
  meta.appendChild(el("div", "rec-reason", row.reason));
  wrap.appendChild(meta);
  return wrap;
}

/* ---------------------------------------------------------------- test */

async function recsTest() {
  const decks = [0, 1, 2, 3].map((i) => (document.getElementById(`deck${i}`) || {}).value || "");
  if (!decks[0].trim()) { toast("Load a deck on the Decks screen first"); return; }
  const x = Math.max(1, Number(document.getElementById("rc-x").value) || 1);
  const y = Math.max(0, Number(document.getElementById("rc-y").value) || 0);
  const seed = Number((document.getElementById("seed") || {}).value) || 0;
  document.getElementById("rc-test").disabled = true;
  document.getElementById("rc-result").textContent = "";
  // Set before the call: the first phase message can arrive before it returns.
  CutLab.owner = "queue";
  CutLab.started = Date.now();
  const started = await call("start_queue_test", JSON.stringify(decks), JSON.stringify(Recs.queue), x, y, seed);
  if (started.error) {
    CutLab.owner = null;
    recsQueueChanged();
    if (started.problems && started.problems.length) {
      document.getElementById("rc-result").appendChild(el("pre", null, started.problems.join("\n")));
    }
    return;
  }
  showStop(true);
}

function onQueueFinished(payload) {
  const host = document.getElementById("rc-result");
  host.textContent = "";
  const queued = new Set(Recs.queue);
  const cuts = payload.verdicts.filter((v) => v.outcome === "cut");
  const out = cuts.filter((v) => !queued.has(v.name));
  const rejected = cuts.filter((v) => queued.has(v.name));
  const accepted = Recs.queue.filter((name) => !rejected.some((v) => v.name === name));

  if (payload.change) host.appendChild(changePanel(payload.change));
  const box = el("div", "queue-verdict");
  if (accepted.length) {
    box.appendChild(el("div", "good-text", `Worth adding: ${accepted.join(", ")}`));
    box.appendChild(el("div", null, `Make room by cutting: ${out.map((v) => v.name).join(", ") || "nothing"}`));
  }
  if (rejected.length) {
    box.appendChild(el("div", "bad-text", `Not better than what you run: ${rejected.map((v) => v.name).join(", ")}`));
  }
  host.appendChild(box);

  const row = el("div", "row");
  const use = el("button", "small", "Make it my active deck");
  use.addEventListener("click", () => {
    useFinalDeck(payload.final_decklist);
    Recs.queue = Recs.queue.filter((name) => !accepted.includes(name));
    recsQueueChanged();
  });
  row.appendChild(use);
  row.appendChild(copyButton("Copy final deck", payload.final_decklist));
  host.appendChild(row);
  if (payload.problems && payload.problems.length) {
    host.appendChild(el("pre", null, payload.problems.join("\n")));
  }
}

/* --------------------------------------------------------------- wiring */

document.querySelectorAll("#rc-mode button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#rc-mode button").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    Recs.mode = button.dataset.value;
    recsFind();
  });
});
document.getElementById("rc-months").addEventListener("change", (event) => {
  document.getElementById("rc-months-custom").hidden = event.target.value !== "custom";
  recsFind();
});
["rc-goal", "rc-sort", "rc-lands"].forEach((id) => {
  document.getElementById(id).addEventListener("change", recsFind);
});
document.getElementById("rc-months-custom").addEventListener("change", recsFind);
document.getElementById("rc-go").addEventListener("click", recsFind);
document.getElementById("rc-test").addEventListener("click", recsTest);
["rc-x", "rc-y"].forEach((id) => document.getElementById(id).addEventListener("input", recsMeters));

document.addEventListener("tabchange", (event) => {
  if (event.detail !== "recs") return;
  // First visit, or the deck changed since: look again.
  if (Recs.loadedFor !== deckText()) recsFind();
});

Backend.ready.then(() => {
  recsLoadQueue();
  recsQueueChanged();
});
