"use strict";

/* The Cut lab tab, and the confidence meter it shares with Recs.
 *
 * The lab itself runs in Python (sim/cutlab.py); this screen collects the
 * list, shows how much the chosen number of games can be trusted *before*
 * anything runs, and presents the verdict - on this tab and on top of the
 * Results screen, which shows the final deck's own run.
 */

const CutLab = {
  pool: null,          // the last cutlab_preview reply
  previewTimer: null,
  started: 0,          // when the current lab began, for the speed estimate
  owner: null,         // "cut" | "queue": which screen the running lab belongs to
};

/* ------------------------------------------------------------ the meter */

/* How far apart two cards' win rates must be before this many games can
 * reliably tell them apart. A card's score is a difference of two win rates,
 * each a proportion near one half, so its standard error is about
 * 1 / sqrt(games the card was in); 2.8 standard errors is the usual "95%
 * confident, 80% of the time" threshold. Effects of single cards are a few
 * percent, so this number is what decides whether a run is worth it. */
function detectable(gamesPerCard) {
  if (!gamesPerCard || gamesPerCard <= 0) return 1;
  return Math.min(1, 2.8 / Math.sqrt(gamesPerCard));
}

const CONFIDENCE_LEVELS = [
  { above: 0.25, label: "Nigh on useless", tone: "bad" },
  { above: 0.12, label: "Rough guess", tone: "bad" },
  { above: 0.06, label: "Reasonable", tone: "warn" },
  { above: 0.03, label: "Good", tone: "good" },
  { above: -1, label: "High confidence", tone: "good" },
];

function setMeter(meterId, labelId, gamesPerCard, what) {
  const meter = document.getElementById(meterId);
  const label = document.getElementById(labelId);
  if (!meter || !label) return;
  const d = detectable(gamesPerCard);
  // Log scale between "can't tell 50 points apart" and "can tell 2 apart".
  const fill = Math.max(0, Math.min(1, Math.log(0.5 / d) / Math.log(0.5 / 0.02)));
  const level = CONFIDENCE_LEVELS.find((entry) => d > entry.above);
  const bar = meter.querySelector(".meter-fill");
  bar.style.width = `${Math.round(fill * 100)}%`;
  meter.className = `meter ${level.tone}`;
  label.textContent = level.label;
  label.title = d >= 1
    ? "Too few games to separate any two cards."
    : `${what}: tells apart cards about ${(d * 100).toFixed(1)} win-rate points apart `
      + `(about ${Math.round(gamesPerCard)} games per card).`;
}

/* Seconds per game on this machine, learned from the last lab that ran. */
function secondsPerGame() {
  try {
    const value = Number(localStorage.getItem("mtgfish.secPerGame"));
    if (value > 0 && value < 60) return value;
  } catch (err) { /* private mode */ }
  return 0.6;
}

function rememberSpeed(seconds, games) {
  if (!games || games < 50 || !seconds) return;
  try { localStorage.setItem("mtgfish.secPerGame", String(seconds / games)); } catch (err) { /* */ }
}

function formatDuration(seconds) {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

function setEta(id, games) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = games > 0
    ? `about ${formatDuration(games * secondsPerGame())} for ${games.toLocaleString()} games`
    : "";
}

/* ----------------------------------------------------------- the inputs */

function clAdvanced() {
  return document.getElementById("cl-advanced").checked;
}

function clSave() {
  try {
    ["cl-deck", "cl-core", "cl-unsure", "cl-x", "cl-y", "cl-seed"].forEach((id) => {
      localStorage.setItem(`mtgfish.${id}`, document.getElementById(id).value);
    });
    localStorage.setItem("mtgfish.cl-advanced", clAdvanced() ? "1" : "");
  } catch (err) { /* private mode */ }
}

function clRestore() {
  try {
    ["cl-deck", "cl-core", "cl-unsure", "cl-x", "cl-y", "cl-seed"].forEach((id) => {
      const value = localStorage.getItem(`mtgfish.${id}`);
      if (value !== null) document.getElementById(id).value = value;
    });
    document.getElementById("cl-advanced").checked = !!localStorage.getItem("mtgfish.cl-advanced");
  } catch (err) { /* private mode */ }
  clShowMode();
}

function clShowMode() {
  const advanced = clAdvanced();
  document.getElementById("cl-simple").hidden = advanced;
  document.getElementById("cl-adv").hidden = !advanced;
}

/* The active deck from the Decks screen, split for advanced mode the way the
 * simple mode would split it: commander and lands core, the rest unsure. */
async function clFromDeck(source) {
  const text = typeof source === "string"
    ? source : (document.getElementById("deck0") || {}).value || "";
  if (!text.trim()) { setStatus("there is no active deck on the Decks screen", true); return; }
  if (!clAdvanced()) {
    document.getElementById("cl-deck").value = text;
  } else {
    const view = await call("deck_view", text);
    if (view.error) return;
    const core = view.cards.filter((c) => c.is_land).map((c) => `${c.quantity} ${c.name}`);
    const unsure = view.cards.filter((c) => !c.is_land).map((c) => `${c.quantity} ${c.name}`);
    if (view.commanders.length) {
      core.push("", "// Commander", ...view.commanders.map((c) => `1 ${c.name}`));
    }
    document.getElementById("cl-core").value = core.join("\n");
    document.getElementById("cl-unsure").value = unsure.join("\n");
  }
  clSave();
  clPreview();
}

function clPreviewSoon() {
  clSave();
  clearTimeout(CutLab.previewTimer);
  CutLab.previewTimer = setTimeout(clPreview, 500);
}

async function clPreview() {
  const summary = document.getElementById("cl-summary");
  const deck = document.getElementById("cl-deck").value;
  const core = document.getElementById("cl-core").value;
  const unsure = document.getElementById("cl-unsure").value;
  if (!(clAdvanced() ? core.trim() || unsure.trim() : deck.trim())) {
    CutLab.pool = null;
    summary.textContent = "";
    clMeters();
    return;
  }
  const pool = await call("cutlab_preview", deck, core, unsure, clAdvanced());
  if (pool.error) return;
  CutLab.pool = pool;
  const parts = [
    `${pool.fixed} fixed`,
    `${pool.unsure} on trial for ${Math.max(0, pool.slots)} slots`,
  ];
  if (pool.commanders.length) parts.unshift(pool.commanders.join(" + "));
  summary.textContent = pool.cuts > 0
    ? `${parts.join(" · ")} → ${pool.cuts} to cut`
    : `${parts.join(" · ")} → nothing to cut, it already fits`;
  summary.className = pool.cuts > 0 ? "hint small" : "hint small warn-text";
  if (pool.problems.length) summary.title = pool.problems.join("\n");
  clMeters();
}

/* Games per card in each phase, from the pool's shape. Phase 1: each deck
 * holds slots/unsure of the cards on trial. Phase 2: about twice as many
 * contenders as cuts, each in about half the decks, with a tenth of the
 * budget kept for the final deck's report. */
function clMeters() {
  const x = Number(document.getElementById("cl-x").value) || 0;
  const y = Number(document.getElementById("cl-y").value) || 0;
  const pool = CutLab.pool;
  const share = pool && pool.unsure ? Math.max(0, pool.slots) / pool.unsure : 0.75;
  setMeter("cl-xmeter", "cl-xlabel", x * share, "Phase 1");
  setMeter("cl-ymeter", "cl-ylabel", y * 0.9 * 0.5, "Phase 2");
  setEta("cl-eta", x + y);
}

/* ------------------------------------------------------------ the run */

async function clRun() {
  const deck = document.getElementById("cl-deck").value;
  const core = document.getElementById("cl-core").value;
  const unsure = document.getElementById("cl-unsure").value;
  const x = Math.max(0, Number(document.getElementById("cl-x").value) || 0);
  const y = Math.max(0, Number(document.getElementById("cl-y").value) || 0);
  const seed = Number(document.getElementById("cl-seed").value) || 0;
  if (x + y < 1) { setStatus("give the lab some games to play", true); return; }

  const opponents = [1, 2, 3].map((i) => (document.getElementById(`deck${i}`) || {}).value || "");
  const button = document.getElementById("cl-run");
  button.disabled = true;
  document.getElementById("cl-results").textContent = "";
  setStatus("starting the cut lab...");
  // Set before the call: the first phase message can arrive before it returns.
  CutLab.owner = "cut";
  CutLab.started = Date.now();
  const started = await call(
    "start_cutlab", JSON.stringify([deck, ...opponents]), core, unsure, clAdvanced(), x, y, seed
  );
  if (started.error) {
    CutLab.owner = null;
    button.disabled = false;
    if (started.problems && started.problems.length) {
      document.getElementById("cl-results").appendChild(el("pre", null, started.problems.join("\n")));
    }
    return;
  }
  showStop(true);
}

function onCutlabStatus(text) {
  const target = CutLab.owner === "queue" ? "rc-phase" : "cl-phase";
  const node = document.getElementById(target);
  if (node) node.textContent = text;
}

function onCutlabFinished(payload) {
  const owner = payload.kind || CutLab.owner;
  CutLab.owner = null;
  document.getElementById("cl-run").disabled = false;
  const test = document.getElementById("rc-test");
  if (test && typeof recsQueueChanged === "function") recsQueueChanged();
  const bar = document.getElementById("progress");
  if (bar) bar.style.display = "none";
  showStop(false);
  ["cl-phase", "rc-phase"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.textContent = "";
  });
  if (payload.cancelled) { setStatus("lab stopped - no results were kept"); return; }
  if (payload.error) { setStatus(payload.error, true); return; }

  rememberSpeed((Date.now() - CutLab.started) / 1000, payload.played);
  clMeters();
  if (typeof recsMeters === "function") recsMeters();

  if (owner === "queue") {
    if (typeof onQueueFinished === "function") onQueueFinished(payload);
  } else {
    drawCutResults(document.getElementById("cl-results"), payload);
  }
  showCutVerdict(payload);

  if (payload.report) {
    report = payload.report;
    setStatus(`${owner === "queue" ? "queue test" : "cut lab"} done - showing the final deck`);
    showTab("results");
  } else {
    setStatus("lab done - no games were left for the final deck's report");
  }
}

/* ---------------------------------------------------------- the verdict */

function scoreText(value) {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}`;
}

function verdictTable(rows, caption) {
  const table = el("table", "verdicts");
  if (caption) table.appendChild(el("caption", null, caption));
  const head = el("tr");
  ["Card", "Score", "±", "Games in", "Why"].forEach((title) => head.appendChild(el("th", null, title)));
  const thead = el("thead");
  thead.appendChild(head);
  table.appendChild(thead);
  const body = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr", row.outcome);
    tr.appendChild(el("td", null, row.name));
    tr.appendChild(el("td", "num", row.games ? scoreText(row.score) : "—"));
    tr.appendChild(el("td", "num dim", row.games ? (row.margin * 100).toFixed(1) : ""));
    tr.appendChild(el("td", "num dim", row.games ? String(row.games) : ""));
    tr.appendChild(el("td", "dim", row.stage));
    body.appendChild(tr);
  });
  table.appendChild(body);
  return table;
}

function copyButton(label, text) {
  const button = el("button", "ghost small", label);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
    } catch (err) {
      setStatus("could not copy - select the text instead", true);
    }
  });
  return button;
}

/* Make the lab's final deck the active deck on the Decks screen. */
function useFinalDeck(text) {
  const box = document.getElementById("deck0");
  if (!box) return;
  box.value = text;
  saveSlot(0, text);
  validateDeck(0);
  if (typeof loadDeckView === "function") loadDeckView(true);
  toast("The final deck is now your active deck");
}

function drawCutResults(host, payload) {
  host.textContent = "";
  const cuts = payload.verdicts.filter((v) => v.outcome === "cut");
  const kept = payload.verdicts.filter((v) => v.outcome === "kept");
  const core = payload.verdicts.filter((v) => v.outcome === "core");

  const head = el("div", "panel cut-head");
  head.appendChild(el("h2", null, `Cut these ${cuts.length}`));
  const list = el("ul", "cutlist");
  cuts.sort((a, b) => a.score - b.score).forEach((v) => {
    const item = el("li");
    item.appendChild(el("strong", null, v.name));
    item.appendChild(el("span", "dim", ` ${v.stage}`));
    list.appendChild(item);
  });
  head.appendChild(list);
  const actions = el("div", "row");
  actions.appendChild(copyButton("Copy cut list", cuts.map((v) => v.name).join("\n")));
  actions.appendChild(copyButton("Copy final deck", payload.final_decklist));
  const use = el("button", "small", "Make it my active deck");
  use.addEventListener("click", () => useFinalDeck(payload.final_decklist));
  actions.appendChild(use);
  if (payload.report) {
    const open = el("button", "primary small", "Open the final deck's results");
    open.addEventListener("click", () => { report = payload.report; showTab("results"); });
    actions.appendChild(open);
  }
  head.appendChild(actions);
  head.appendChild(el("p", "hint small",
    `${payload.played.toLocaleString()} games played. Score: win rate when the card was `
    + "drawn minus when it sat undrawn in the library, in percentage points, shrunk "
    + "towards the average for cards seen in few games. ± is a 95% interval."));
  host.appendChild(head);

  if (payload.change) host.appendChild(changePanel(payload.change));

  if (payload.rounds && payload.rounds.length) {
    const rounds = el("details", "panel");
    rounds.appendChild(el("summary", null, `Phase 2: ${payload.rounds.length} elimination round(s)`));
    payload.rounds.forEach((round) => {
      rounds.appendChild(el("div", "hint small",
        `Round ${round.round}: ${round.decks} decks x ${round.games / Math.max(1, round.decks)} games - `
        + `dropped ${round.dropped.join(", ")}`));
    });
    host.appendChild(rounds);
  }

  const all = el("details", "panel");
  all.appendChild(el("summary", null, `Every card on trial (${payload.verdicts.length})`));
  if (kept.length) all.appendChild(verdictTable(kept, "Survived phase 2"));
  if (core.length) all.appendChild(verdictTable(core, "Core after phase 1"));
  if (cuts.length) all.appendChild(verdictTable(cuts, "Cut"));
  host.appendChild(all);

  if (payload.problems && payload.problems.length) {
    host.appendChild(el("pre", null, payload.problems.join("\n")));
  }
}

function changePanel(change) {
  const box = el("div", "panel");
  const delta = change.delta * 100;
  box.appendChild(el("strong", delta >= 0 ? "good-text" : "bad-text",
    `Win rate ${(change.before * 100).toFixed(1)}% → ${(change.after * 100).toFixed(1)}% `
    + `(${delta >= 0 ? "+" : ""}${delta.toFixed(1)} points)`));
  box.appendChild(el("div", "hint small",
    `Over the ${change.games.toLocaleString()} seeds both decks played - a paired comparison.`));
  return box;
}

/* A compact copy of the verdict above the Results charts, so the person
 * sent there by the lab still sees what to cut. Cleared by an ordinary run. */
function showCutVerdict(payload) {
  const host = document.getElementById("cutverdict");
  if (!host) return;
  host.textContent = "";
  const cuts = payload.verdicts.filter((v) => v.outcome === "cut");
  const box = el("div", "panel cut-banner");
  const title = payload.kind === "queue" ? "Queue test: the final deck" : "Cut lab: the final deck";
  box.appendChild(el("strong", null, title));
  box.appendChild(el("div", "hint small",
    cuts.length ? `Cut: ${cuts.map((v) => v.name).join(", ")}` : "Nothing was cut."));
  if (payload.change) {
    const delta = payload.change.delta * 100;
    box.appendChild(el("div", "hint small",
      `Win rate change on shared seeds: ${delta >= 0 ? "+" : ""}${delta.toFixed(1)} points`));
  }
  const row = el("div", "row");
  row.appendChild(copyButton("Copy final deck", payload.final_decklist));
  const use = el("button", "small", "Make it my active deck");
  use.addEventListener("click", () => useFinalDeck(payload.final_decklist));
  row.appendChild(use);
  const back = el("button", "ghost small", payload.kind === "queue" ? "Back to Recs" : "Back to the Cut lab");
  back.addEventListener("click", () => showTab(payload.kind === "queue" ? "recs" : "cutlab"));
  row.appendChild(back);
  box.appendChild(row);
  host.appendChild(box);
  host.hidden = false;
}

/* --------------------------------------------------------------- wiring */

Backend.ready.then(() => {
  // A tab whose slot this server lacks hides rather than failing on click.
  document.querySelectorAll("nav button[data-requires]").forEach((button) => {
    button.hidden = !Backend.has(button.dataset.requires);
  });
  Backend.on("cutlab_status", onCutlabStatus);
  Backend.on("cutlab_finished", (payload) => onCutlabFinished(JSON.parse(payload)));
  // An ordinary run replaces "the last run", so the lab's banner goes.
  Backend.on("run_finished", () => { document.getElementById("cutverdict").hidden = true; });
  clRestore();
  clPreview();
});

document.getElementById("cl-advanced").addEventListener("change", () => {
  clShowMode();
  clSave();
  // Switching to advanced with empty boxes: split whatever was pasted.
  const core = document.getElementById("cl-core").value.trim();
  const unsure = document.getElementById("cl-unsure").value.trim();
  if (clAdvanced() && !core && !unsure && document.getElementById("cl-deck").value.trim()) {
    clFromDeck(document.getElementById("cl-deck").value);
    return;
  }
  clPreview();
});
["cl-deck", "cl-core", "cl-unsure"].forEach((id) => {
  document.getElementById(id).addEventListener("input", clPreviewSoon);
});
["cl-x", "cl-y", "cl-seed"].forEach((id) => {
  document.getElementById(id).addEventListener("input", () => { clSave(); clMeters(); });
});
document.getElementById("cl-fromdeck").addEventListener("click", () => clFromDeck());
document.getElementById("cl-run").addEventListener("click", clRun);
document.addEventListener("tabchange", (event) => {
  if (event.detail === "cutlab" && !document.getElementById("cl-deck").value.trim()
      && !clAdvanced() && (document.getElementById("deck0") || {}).value) {
    // First visit: start from the active deck, as promised.
    document.getElementById("cl-deck").value = document.getElementById("deck0").value;
    clPreviewSoon();
  }
});
