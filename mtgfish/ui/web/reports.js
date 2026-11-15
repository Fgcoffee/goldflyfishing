"use strict";

/* Reports: telling us what's wrong (mtgfish/web/reports.py).
 *
 * Three ways in, one dialog:
 * - "Report a problem" in the top bar, on every page: the person's words,
 *   the page, and the run or deck in view.
 * - "Report this reading" in a card window (Replay's board, the Deck tab's
 *   drawer): the card, the reading the person saw, and what's wrong with it.
 * - "Report this step" in Replay: the run, game, seed and the step's index in
 *   the game log, so the game can be replayed to exactly that point.
 *
 * And, for admins (/auth/me says so), the Reports tab: the review list.
 */

const Reports = (() => {
  const dialog = () => document.getElementById("dlg-report");
  let pending = null; // what the open dialog will send, less the person's words

  function activePage() {
    const tab = document.querySelector("nav button[data-tab].active");
    return tab ? tab.dataset.tab : "";
  }

  /* The run in view: a browser run (played or opened from History), or a
   * server run opened from History. */
  function runRef() {
    const browser = typeof BrowserRuns !== "undefined" ? BrowserRuns.current : null;
    if (browser) {
      const run = (browser.stored && browser.stored.run) || {};
      return {
        run_kind: "browser",
        run_id: run.id || "",
        engine_build: run.engine_build || "",
        ir_build: run.ir_build || "",
      };
    }
    if (typeof Library !== "undefined" && Library.openRunId) {
      return { run_kind: "server", run_id: Library.openRunId };
    }
    return { run_kind: "", run_id: "" };
  }

  /* The decks in the paste boxes, trimmed: enough to rebuild what was run. */
  function decks() {
    const out = [];
    for (let i = 0; i < 4; i += 1) {
      const box = document.getElementById(`deck${i}`);
      const text = box ? box.value.trim() : "";
      if (text) out.push(text.slice(0, 8000));
    }
    return out;
  }

  /* The IR hash a browser run played this card with, when there is one. */
  function cardHashFor(oracleId) {
    const browser = typeof BrowserRuns !== "undefined" ? BrowserRuns.current : null;
    const cards = browser && browser.stored && browser.stored.setup && browser.stored.setup.cards;
    const doc = cards && oracleId ? cards[oracleId] : null;
    return (doc && doc.card_hash) || "";
  }

  function open({ title, about, report, problemChoice = false, textLabel }) {
    pending = { page: activePage(), ...report };
    document.getElementById("rep-title").textContent = title;
    document.getElementById("rep-about").textContent = about || "";
    document.getElementById("rep-problem-label").hidden = !problemChoice;
    document.getElementById("rep-problem").value = "missing";
    document.getElementById("rep-text-label").textContent = textLabel || "What's wrong, in your own words";
    document.getElementById("rep-text").value = "";
    document.getElementById("rep-status").textContent = "";
    document.getElementById("rep-send").disabled = false;
    dialog().showModal();
    document.getElementById("rep-text").focus();
  }

  async function submit(event) {
    event.preventDefault();
    if (pending === null) return;
    const text = document.getElementById("rep-text").value.trim();
    const status = document.getElementById("rep-status");
    if (!text) { status.textContent = "Say what's wrong first."; return; }
    const body = { ...pending, text };
    if (!document.getElementById("rep-problem-label").hidden) {
      body.problem = document.getElementById("rep-problem").value;
    }
    document.getElementById("rep-send").disabled = true;
    status.textContent = "Sending...";
    let result;
    try {
      result = await authPost("/api/reports", body);
    } catch (err) {
      result = { ok: false, data: { error: "couldn't reach the server" } };
    }
    if (!result.ok) {
      status.textContent = (result.data && result.data.error) || `the server said ${result.status}`;
      document.getElementById("rep-send").disabled = false;
      return;
    }
    pending = null;
    dialog().close();
    setStatus(result.data.signed_in
      ? "report sent - thank you. Its status shows in your account (click your name)."
      : "report sent - thank you.");
  }

  /* -- the three ways in ------------------------------------------------- */

  function problem() {
    const run = runRef();
    open({
      title: "Report a problem",
      about: "Anything that's wrong or confusing on this page.",
      report: {
        kind: "problem",
        ...run,
        context: JSON.stringify({ decks: decks() }).slice(0, 20000),
      },
    });
  }

  /* `card` is a board card or a deck row ({name, oracle_id?}); `reading` is
   * the reading as the person saw it, as text. */
  function card(cardInfo, reading) {
    const run = runRef();
    open({
      title: `Report this reading: ${cardInfo.name}`,
      about: "What does the engine get wrong about this card? Say what the card should do.",
      problemChoice: true,
      textLabel: "What it should do, in your own words",
      report: {
        kind: "card",
        card_name: cardInfo.name,
        oracle_id: cardInfo.oracle_id || "",
        card_hash: cardHashFor(cardInfo.oracle_id),
        ir_build: run.ir_build || "",
        reading: (reading || "").slice(0, 20000),
        run_kind: run.run_kind,
        run_id: run.run_id,
      },
    });
  }

  /* `where` is {game_index, seed, log_index, text}: the step as Replay shows it. */
  function step(where) {
    const run = runRef();
    open({
      title: "Report this step",
      about: `Game ${where.game_index}, at: ${where.text || `log line ${where.log_index}`}`,
      textLabel: "What went wrong here, and what should have happened",
      report: {
        kind: "step",
        ...run,
        game_index: where.game_index,
        seed: where.seed,
        log_index: where.log_index,
        context: JSON.stringify({ decks: decks(), step: where.text || "" }).slice(0, 20000),
      },
    });
  }

  /* -- the review list (admins) -------------------------------------------- */

  const review = { status: "new", statuses: [], counts: {} };
  const STATUS_LABELS = {
    new: "New", confirmed: "Confirmed", fixed: "Fixed", not_a_bug: "Not a bug", duplicate: "Duplicate",
  };

  async function loadReview() {
    const msg = document.getElementById("rep-msg");
    let response;
    try {
      const kind = document.getElementById("rep-kind").value;
      response = await fetch(`/api/admin/reports?status=${encodeURIComponent(review.status)}`
        + `&kind=${encodeURIComponent(kind)}`, { credentials: "same-origin" });
    } catch (err) {
      msg.textContent = "couldn't reach the server";
      return;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { msg.textContent = data.error || `the server said ${response.status}`; return; }
    msg.textContent = "";
    review.statuses = data.statuses || [];
    review.counts = data.counts || {};
    drawFilter();
    drawList(data.reports || []);
  }

  function drawFilter() {
    const host = document.getElementById("rep-filter");
    host.textContent = "";
    review.statuses.forEach((status) => {
      const button = el("button", status === review.status ? "active" : null,
        `${STATUS_LABELS[status] || status} (${review.counts[status] || 0})`);
      button.addEventListener("click", () => { review.status = status; loadReview(); });
      host.appendChild(button);
    });
  }

  function field(host, label, value) {
    if (value === null || value === undefined || value === "") return;
    const row = el("div", "rep-field");
    row.appendChild(el("span", "label", label));
    row.appendChild(el("span", null, String(value)));
    host.appendChild(row);
  }

  function drawList(reports) {
    const host = document.getElementById("rep-list");
    host.textContent = "";
    if (!reports.length) { host.appendChild(el("p", "hint", "Nothing here.")); return; }
    reports.forEach((report) => {
      const item = el("div", "rep-item");
      const head = el("div", "rep-head");
      const what = report.kind === "card" ? `Card: ${report.card_name} (${report.problem})`
        : report.kind === "step" ? `Step: game ${report.game_index}, log line ${report.log_index}`
          : "Problem";
      head.appendChild(el("strong", null, what));
      head.appendChild(el("span", "dim", ` ${libDateSafe(report.created_at)} · ${report.page || "?"}`
        + (report.signed_in ? " · signed in" : " · anonymous")));
      item.appendChild(head);
      item.appendChild(el("div", "rep-text", report.text));
      const details = el("div", "rep-details");
      field(details, "run", report.run_id ? `${report.run_kind} ${report.run_id}` : "");
      field(details, "seed", report.seed);
      field(details, "oracle id", report.oracle_id);
      field(details, "card hash", report.card_hash);
      field(details, "IR build", report.ir_build);
      field(details, "engine build", report.engine_build);
      item.appendChild(details);
      if (report.reading) {
        const reading = el("details");
        reading.appendChild(el("summary", null, "The reading they saw"));
        reading.appendChild(el("pre", "rep-pre", report.reading));
        item.appendChild(reading);
      }
      if (report.context) {
        const context = el("details");
        context.appendChild(el("summary", null, "Context (decks, step)"));
        context.appendChild(el("pre", "rep-pre", report.context));
        item.appendChild(context);
      }
      const actions = el("div", "row compact");
      const select = document.createElement("select");
      review.statuses.forEach((status) => {
        const option = document.createElement("option");
        option.value = status;
        option.textContent = STATUS_LABELS[status] || status;
        option.selected = status === report.status;
        select.appendChild(option);
      });
      const note = document.createElement("input");
      note.type = "text";
      note.placeholder = "Note (e.g. the finding or commit)";
      note.value = report.note || "";
      note.maxLength = 4000;
      const save = el("button", "small", "Save");
      save.addEventListener("click", async () => {
        save.disabled = true;
        const result = await authPost(`/api/admin/reports/${report.id}`, { status: select.value, note: note.value });
        save.disabled = false;
        if (!result.ok) { document.getElementById("rep-msg").textContent = result.data.error || "not saved"; return; }
        loadReview();
      });
      actions.appendChild(select);
      actions.appendChild(note);
      actions.appendChild(save);
      item.appendChild(actions);
      host.appendChild(item);
    });
  }

  function libDateSafe(iso) {
    return typeof libDate === "function" ? libDate(iso) : iso;
  }

  /* -- your reports, in the account dialog ------------------------------------ */

  async function drawMine(host) {
    if (!host) return;
    host.textContent = "";
    let data = {};
    try {
      const response = await fetch("/api/reports/mine", { credentials: "same-origin" });
      if (!response.ok) return;
      data = await response.json();
    } catch (err) {
      return;
    }
    const reports = data.reports || [];
    if (!reports.length) return;
    host.appendChild(el("div", "stack-label", "Your reports"));
    const list = el("ul", "rep-mine");
    reports.slice(0, 20).forEach((report) => {
      const what = report.kind === "card" ? report.card_name
        : report.kind === "step" ? `game ${report.game_index}, step ${report.log_index}` : "a problem";
      const item = el("li", null, `${what}: ${STATUS_LABELS[report.status] || report.status}`);
      item.title = report.text + (report.note ? `\n\nOur note: ${report.note}` : "");
      list.appendChild(item);
    });
    host.appendChild(list);
  }

  /* -- wiring --------------------------------------------------------------- */

  document.getElementById("rep-form").addEventListener("submit", submit);
  document.getElementById("btn-report").addEventListener("click", problem);
  document.getElementById("rep-refresh").addEventListener("click", loadReview);
  document.getElementById("rep-kind").addEventListener("change", loadReview);
  document.addEventListener("tabchange", (event) => { if (event.detail === "reports") loadReview(); });
  document.addEventListener("authchange", () => {
    document.querySelector('nav button[data-tab="reports"]').hidden = !Auth.admin;
  });

  return { problem, card, step, loadReview, drawMine };
})();
