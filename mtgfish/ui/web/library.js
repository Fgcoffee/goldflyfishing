"use strict";

/* My decks and History: saved decks (with versions) and past runs.
 *
 * Web only, and only when signed in. Runs are saved by the server when a
 * signed-in, verified account's simulation finishes; this screen lists them,
 * opens a past report on the Results screen (replays included), re-runs one
 * with the exact same decks and seed, or compares two side by side.
 */

const Library = {
  decks: [],
  runs: [],
  picked: new Set(),   // run ids ticked for comparison
  loadedDeck: null,    // {id, name} of the saved deck in slot 1, if any
};

async function libGet(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

function libSay(id, text, bad) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = text || "";
  node.className = bad ? "hint bad" : "hint";
}

function libSignedOut(hostId, what) {
  const host = document.getElementById(hostId);
  host.textContent = "";
  const box = el("div", "empty");
  box.appendChild(el("h2", null, `Log in to see your ${what}`));
  box.appendChild(el("p", null, "Simulations work without an account; saving needs one."));
  const button = el("button", "primary", "Log in / Register");
  button.addEventListener("click", () => document.getElementById("btn-login").click());
  box.appendChild(button);
  host.appendChild(box);
}

function libDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
    + " " + date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/* ------------------------------------------------------------ my decks */

async function libLoadDecks() {
  if (!Auth.user) { libSignedOut("lib-decks", "decks"); return; }
  const reply = await libGet("/api/decks");
  if (!reply.ok) { libSay("lib-deckmsg", reply.data.error, true); return; }
  Library.decks = reply.data.decks;
  libDrawDecks();
}

function libDrawDecks() {
  const host = document.getElementById("lib-decks");
  host.textContent = "";
  if (!Library.decks.length) {
    host.appendChild(el("p", "hint", "No saved decks yet. Save the deck in slot 1 with the button above."));
    return;
  }
  const table = el("table", "libtable");
  const head = el("tr");
  ["Deck", "Commander", "Version", "Updated", ""].forEach((t) => head.appendChild(el("th", null, t)));
  table.appendChild(head);
  Library.decks.forEach((deck) => {
    const tr = el("tr");
    tr.appendChild(el("td", "strong", deck.name));
    tr.appendChild(el("td", "dim", deck.commander || ""));
    tr.appendChild(el("td", "dim", `v${deck.version}`));
    tr.appendChild(el("td", "dim", libDate(deck.updated_at)));
    const actions = el("td", "actions");
    const add = (label, fn, cls) => {
      const button = el("button", `small ${cls || ""}`, label);
      button.addEventListener("click", fn);
      actions.appendChild(button);
    };
    add("Load", () => libLoadDeck(deck), "primary");
    add("Rename", () => libRenameDeck(deck));
    add("Delete", () => libDeleteDeck(deck), "ghost danger");
    tr.appendChild(actions);
    table.appendChild(tr);
  });
  host.appendChild(table);
}

async function libSaveDeck() {
  const text = (document.getElementById("deck0") || {}).value || "";
  if (!text.trim()) { libSay("lib-deckmsg", "Paste a deck into slot 1 on the Decks screen first.", true); return; }
  const current = Library.loadedDeck;
  let result;
  if (current && confirm(`Save as a new version of "${current.name}"?\n(Cancel to save it as a new deck.)`)) {
    result = await authPost(`/api/decks/${current.id}`, { decklist: text });
  } else {
    const name = prompt("Name this deck:", "");
    if (name === null) return;
    result = await authPost("/api/decks", { name, decklist: text });
  }
  if (!result.ok) { libSay("lib-deckmsg", result.data.error, true); return; }
  Library.loadedDeck = { id: result.data.deck.id, name: result.data.deck.name };
  libSay("lib-deckmsg", `Saved "${result.data.deck.name}" (v${result.data.deck.version}).`);
  libLoadDecks();
}

async function libLoadDeck(deck) {
  const reply = await libGet(`/api/decks/${deck.id}`);
  if (!reply.ok) { libSay("lib-deckmsg", reply.data.error, true); return; }
  const box = document.getElementById("deck0");
  box.value = reply.data.decklist;
  saveSlot(0, box.value);
  validateDeck(0);
  Library.loadedDeck = { id: deck.id, name: deck.name };
  toast(`Loaded "${deck.name}" into slot 1`);
  showTab("setup");
}

async function libRenameDeck(deck) {
  const name = prompt("New name:", deck.name);
  if (!name) return;
  const result = await authPost(`/api/decks/${deck.id}`, { name });
  if (!result.ok) { libSay("lib-deckmsg", result.data.error, true); return; }
  libLoadDecks();
}

async function libDeleteDeck(deck) {
  if (!confirm(`Delete "${deck.name}" and all its versions? Its runs stay in History.`)) return;
  const result = await authPost(`/api/decks/${deck.id}/delete`);
  if (!result.ok) { libSay("lib-deckmsg", result.data.error, true); return; }
  if (Library.loadedDeck && Library.loadedDeck.id === deck.id) Library.loadedDeck = null;
  libLoadDecks();
}

/* -------------------------------------------------------------- history */

async function libLoadRuns() {
  if (!Auth.user) { libSignedOut("lib-runs", "history"); return; }
  const reply = await libGet("/api/runs");
  if (!reply.ok) { libSay("lib-runmsg", reply.data.error, true); return; }
  Library.runs = reply.data.runs;
  Library.picked = new Set([...Library.picked].filter((id) => Library.runs.some((r) => r.id === id)));
  libDrawRuns();
}

function libDrawRuns() {
  const host = document.getElementById("lib-runs");
  host.textContent = "";
  const note = Auth.user && !(Auth.user.email_verified || Auth.user.discord)
    ? "Verify your email to start keeping history." : "";
  if (!Library.runs.length) {
    host.appendChild(el("p", "hint", note || "No runs yet. Simulations you run while logged in appear here."));
    return;
  }
  const table = el("table", "libtable");
  const head = el("tr");
  ["", "When", "Deck", "Games", "Seed", "Win rate", ""].forEach((t) => head.appendChild(el("th", null, t)));
  table.appendChild(head);
  Library.runs.forEach((run) => {
    const tr = el("tr");
    const pick = el("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = Library.picked.has(run.id);
    box.title = "tick two runs to compare them";
    box.addEventListener("change", () => {
      if (box.checked) Library.picked.add(run.id); else Library.picked.delete(run.id);
      document.getElementById("lib-compare").disabled = Library.picked.size !== 2;
    });
    pick.appendChild(box);
    tr.appendChild(pick);
    tr.appendChild(el("td", "dim", libDate(run.created_at)));
    tr.appendChild(el("td", "strong", run.label));
    tr.appendChild(el("td", "dim", String(run.games)));
    tr.appendChild(el("td", "dim", String(run.run_seed)));
    tr.appendChild(el("td", null, run.win_rate == null ? "—" : pct(run.win_rate)));
    const actions = el("td", "actions");
    const add = (label, fn, cls) => {
      const button = el("button", `small ${cls || ""}`, label);
      button.addEventListener("click", fn);
      actions.appendChild(button);
    };
    add("Open", () => libOpenRun(run), "primary");
    add("Re-run", () => libRerun(run));
    add("Delete", () => libDeleteRun(run), "ghost danger");
    tr.appendChild(actions);
    table.appendChild(tr);
  });
  host.appendChild(table);
  document.getElementById("lib-compare").disabled = Library.picked.size !== 2;
}

async function libOpenRun(run) {
  const result = await authPost(`/api/runs/${run.id}/open`);
  if (!result.ok) { libSay("lib-runmsg", result.data.error, true); return; }
  report = result.data.report;
  setStatus(`showing a past run: ${run.label}`);
  showTab("results");
}

async function libRerun(run) {
  const reply = await libGet(`/api/runs/${run.id}`);
  if (!reply.ok) { libSay("lib-runmsg", reply.data.error, true); return; }
  const config = reply.data.run.config;
  const lists = config.decklists || [];
  for (let i = 0; i < 4; i += 1) {
    const box = document.getElementById(`deck${i}`);
    if (!box) continue;
    // Opponent slots that held a copy of the deck are left empty, which
    // the server fills with a copy again - the same table either way.
    box.value = i === 0 || lists[i] !== lists[0] ? (lists[i] || "") : "";
    saveSlot(i, box.value);
  }
  document.getElementById("games").value = config.games;
  document.getElementById("seed").value = config.run_seed;
  validateDeck(0);
  showTab("setup");
  toast("Same decks, games and seed loaded - press Simulate to run it again");
}

async function libDeleteRun(run) {
  if (!confirm(`Remove this run (${run.label}) from your history?`)) return;
  const result = await authPost(`/api/runs/${run.id}/delete`);
  if (!result.ok) { libSay("lib-runmsg", result.data.error, true); return; }
  Library.picked.delete(run.id);
  libLoadRuns();
}

async function libCompare() {
  const ids = [...Library.picked];
  if (ids.length !== 2) return;
  const [a, b] = await Promise.all(ids.map((id) => libGet(`/api/runs/${id}`)));
  if (!a.ok || !b.ok) { libSay("lib-runmsg", "could not load both runs", true); return; }
  const left = a.data.run;
  const right = b.data.run;
  const host = document.getElementById("lib-comparison");
  host.textContent = "";
  const table = el("table", "libtable compare");
  const head = el("tr");
  ["", left.label, right.label, "Difference"].forEach((t) => head.appendChild(el("th", null, t)));
  table.appendChild(head);
  const metric = (label, pick, format, higherIsBetter) => {
    const tr = el("tr");
    tr.appendChild(el("td", "dim", label));
    const x = pick(left);
    const y = pick(right);
    tr.appendChild(el("td", null, x == null ? "—" : format(x)));
    tr.appendChild(el("td", null, y == null ? "—" : format(y)));
    if (x == null || y == null || higherIsBetter === null) {
      tr.appendChild(el("td", "dim", ""));
    } else {
      tr.appendChild(labDelta(y - x, format, higherIsBetter));
    }
    table.appendChild(tr);
  };
  const rep = (run) => run.report || {};
  metric("Win rate", (r) => rep(r).win_rate, pct, true);
  metric("Average win turn", (r) => rep(r).average_win_round, (v) => v.toFixed(2), false);
  metric("Stall-outs", (r) => rep(r).stall_rate, pct, false);
  metric("Games", (r) => r.games, String, null);
  metric("Seed", (r) => r.run_seed, String, null);
  host.appendChild(table);
  const sameSeed = left.run_seed === right.run_seed && left.card_db_hash === right.card_db_hash;
  host.appendChild(el("p", "hint small", sameSeed
    ? "Same seed and card data: the two runs dealt from the same shuffles wherever their decks allow."
    : "Different seeds or card data: part of any difference is run-to-run noise. "
      + "For a fair one-card comparison use the Swap lab."));
}

/* --------------------------------------------------------------- wiring */

document.getElementById("lib-save").addEventListener("click", libSaveDeck);
document.getElementById("lib-compare").addEventListener("click", libCompare);
document.addEventListener("tabchange", (event) => {
  if (event.detail === "mydecks") libLoadDecks();
  if (event.detail === "history") libLoadRuns();
});
document.addEventListener("authchange", () => {
  const visible = Auth.available;
  document.querySelectorAll('nav button[data-tab="mydecks"], nav button[data-tab="history"]')
    .forEach((button) => { button.hidden = !visible; });
  const active = document.querySelector("nav button.active");
  if (active && active.dataset.tab === "mydecks") libLoadDecks();
  if (active && active.dataset.tab === "history") libLoadRuns();
});
Backend.on && Backend.ready.then(() => {
  Backend.on("history_changed", () => {
    const active = document.querySelector("nav button.active");
    if (active && active.dataset.tab === "history") libLoadRuns();
  });
});
