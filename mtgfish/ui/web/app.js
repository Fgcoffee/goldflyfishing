"use strict";

/* The whole front end.
 *
 * No framework: this is four screens over one bridge object, and a framework
 * would be more code than the thing it manages. Every call to Python returns a
 * JSON string, so `call()` is the only place that knows about the transport.
 */

let report = null;
let sandboxState = null;
let pendingAction = null;   // an action waiting for the operator to pick targets
let chosenCard = null;

/* ------------------------------------------------------------------ setup */

/* transport.js decides between the desktop window's QWebChannel and the web
 * server; everything below is the same either way. */
Backend.ready.then(() => {
  Backend.on("progressed", (done, total) => setProgress(done, total));
  // The run happens on a worker thread, so its result arrives as a signal
  // rather than as the return value of the call that started it. Awaiting the
  // call is what froze the window.
  Backend.on("run_finished", (payload) => onRunFinished(JSON.parse(payload)));
  Backend.on("lab_finished", (payload) => onLabFinished(JSON.parse(payload)));
  buildDeckSlots();
  labModeHint();
  refreshSandbox();
  setStatus("ready");
  document.dispatchEvent(new CustomEvent("appready"));
}).catch((err) => setStatus(`could not connect: ${err.message}`, true));

async function call(method, ...args) {
  const raw = await Backend.call(method, ...args);
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    parsed = { error: `bad response from ${method}: ${raw}` };
  }
  if (parsed && parsed.error && !parsed.missing) setStatus(parsed.error, true);
  return parsed;
}

/* A theme colour, read live so charts follow the theme switch. */
function themeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/* A real bar, not a number that changes. A run of ten thousand games takes
 * minutes, and a status line that reads "simulating 4200/10000" gives no sense
 * of how much longer - which is the only question being asked. */
function setProgress(done, total) {
  const bar = document.getElementById("progress");
  const fill = document.getElementById("progressfill");
  if (!bar || !fill) { setStatus(`simulating ${done}/${total}`); return; }

  const pct = total ? Math.round((done / total) * 100) : 0;
  bar.style.display = done && done < total ? "block" : "none";
  // Also shown here, not only when Simulate is pressed: a page reloaded
  // mid-run still has a run it may want to stop.
  if (done < total) showStop(true);
  fill.style.width = `${pct}%`;
  setStatus(`simulating ${done} of ${total} games (${pct}%)`);
}

function showStop(visible) {
  const button = document.getElementById("stoprun");
  if (!button) return;
  button.hidden = !(visible && Backend.has("cancel_run"));
  if (visible) button.disabled = false;
}

document.getElementById("stoprun").addEventListener("click", async () => {
  const button = document.getElementById("stoprun");
  button.disabled = true;
  setStatus("stopping the run...");
  const reply = await call("cancel_run");
  if (!reply.cancelled) {
    showStop(false);
    setStatus(reply.reason || "nothing is running");
  }
  // Otherwise the run's finished signal arrives once its workers are gone.
});

function setStatus(text, isError) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = isError ? "err" : "";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------------- tabs */

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
    if (button.dataset.tab === "results" && report) drawCharts();
    document.dispatchEvent(new CustomEvent("tabchange", { detail: button.dataset.tab }));
  });
});

function showTab(name) {
  const button = document.querySelector(`nav button[data-tab="${name}"]`);
  if (button) button.click();
}

/* Deck slots survive a reload. The page is told to reload whenever a new
 * version is deployed, and that must never cost someone the list they pasted. */
function savedSlot(index) {
  try { return localStorage.getItem(`mtgfish.deck${index}`) || ""; } catch (err) { return ""; }
}
function saveSlot(index, text) {
  try { localStorage.setItem(`mtgfish.deck${index}`, text); } catch (err) { /* private mode */ }
}

/* ------------------------------------------------------------------ decks */

const DECK_LABELS = ["Your deck", "Opponent 2", "Opponent 3", "Opponent 4"];

function buildDeckSlots() {
  const host = document.getElementById("deckslots");
  DECK_LABELS.forEach((label, index) => {
    const slot = el("div", "panel deckslot");
    slot.appendChild(el("h3", null, label));
    const box = el("textarea");
    box.id = `deck${index}`;
    box.placeholder = index === 0
      ? "Paste a decklist:\n1 Sol Ring\n1 Llanowar Elves\n...\n\n"
        + "or an Archidekt URL:\nhttps://archidekt.com/decks/1234567"
      : "leave empty to use a copy of your deck";
    slot.appendChild(box);
    const summary = el("div", "summary");
    summary.id = `decksummary${index}`;
    slot.appendChild(summary);
    box.value = savedSlot(index);
    box.addEventListener("input", () => saveSlot(index, box.value));
    box.addEventListener("change", () => validateDeck(index));
    host.appendChild(slot);
    if (box.value.trim()) validateDeck(index);
  });
}

async function validateDeck(index) {
  const text = document.getElementById(`deck${index}`).value.trim();
  const summary = document.getElementById(`decksummary${index}`);
  if (index === 0) document.dispatchEvent(new CustomEvent("deckchange"));
  if (!text) { summary.textContent = ""; return; }

  const info = await call("validate_deck", text);
  if (info.error) { summary.innerHTML = `<span class="bad">${info.error}</span>`; return; }

  // The headline is the share of printed abilities the engine will actually
  // run. Counting cards conflates a creature missing one trigger with a card
  // that does nothing at all, and those are not the same problem.
  const pct = info.abilities
    ? Math.round((info.abilities_understood / info.abilities) * 100)
    : 100;
  const bits = [`${info.cards} cards`];
  if (info.commanders.length) bits.push(`commander: ${info.commanders.join(", ")}`);
  if (info.unresolved.length) bits.push(`<span class="bad">${info.unresolved.length} not found</span>`);
  bits.push(
    `<span class="${pct >= 80 ? "good" : "bad"}">${pct}% of abilities understood</span>`
  );
  if (info.blank.length) bits.push(`<span class="bad">${info.blank.length} blank</span>`);
  if (info.partial.length) bits.push(`<span class="dim">${info.partial.length} partial</span>`);
  if (info.suppressed.length) {
    bits.push(`<span class="dim">${info.suppressed.length} switched off</span>`);
  }
  summary.innerHTML = bits.join(" &middot; ");

  if (index === 0) {
    const lines = [];
    if (info.unresolved.length) lines.push("Not found:\n  " + info.unresolved.join("\n  "));
    if (info.blank.length) {
      lines.push(
        "Blank - nothing on these was understood, so they are a body and a mana "
        + "cost and nothing else:\n  " + info.blank.join("\n  ")
      );
    }
    if (info.partial.length) {
      lines.push(
        "Partly read - some abilities work, some are inert. Look these up in the "
        + "sandbox to see which half:\n  " + info.partial.join("\n  ")
      );
    }
    if (info.suppressed.length) {
      lines.push(
        "Switched off by you - marked inert in the sandbox:\n  "
        + info.suppressed.join("\n  ")
      );
    }
    document.getElementById("deckreport").textContent = lines.join("\n\n");
  }
}

document.getElementById("run").addEventListener("click", async () => {
  const decks = [];
  for (let i = 0; i < 4; i += 1) {
    const text = document.getElementById(`deck${i}`).value.trim();
    if (text) decks.push(text);
  }
  if (!decks.length) { setStatus("paste a decklist first", true); return; }

  const games = Number(document.getElementById("games").value) || 100;
  const seed = Number(document.getElementById("seed").value) || 0;

  const button = document.getElementById("run");
  button.disabled = true;
  setStatus(`simulating ${games} games...`);

  const started = await call("start_run", JSON.stringify(decks), games, seed);
  if (started.error) {
    button.disabled = false;
    setStatus(started.error, true);
  } else {
    showStop(true);
  }
  // Everything else happens in onRunFinished, when the worker reports back.
});

function onRunFinished(payload) {
  const button = document.getElementById("run");
  button.disabled = false;
  const bar = document.getElementById("progress");
  if (bar) bar.style.display = "none";
  showStop(false);
  if (payload.cancelled) { setStatus("run stopped - no results were kept"); return; }
  if (payload.error) { setStatus(payload.error, true); return; }

  report = payload;
  setStatus("run complete");
  document.querySelector('nav button[data-tab="results"]').click();
  if (typeof drawResults === "function") drawResults();
}

/* ---------------------------------------------------------------- results */

function drawCharts() {
  const headline = document.getElementById("headline");
  headline.innerHTML = "";
  const stats = [
    ["Win rate", `${(report.win_rate * 100).toFixed(1)}%`],
    ["Stall-outs", `${(report.stall_rate * 100).toFixed(1)}%`],
    ["Games", report.games],
    ["Avg win turn", report.average_win_turn ? report.average_win_turn.toFixed(1) : "-"],
    ["Commander never cast", report.commander_never],
  ];
  stats.forEach(([label, value]) => {
    const card = el("div", "stat");
    card.appendChild(el("div", "value", String(value)));
    card.appendChild(el("div", "label", label));
    headline.appendChild(card);
  });

  // Above the charts, not below them: if games were cut short the numbers in
  // those charts are measuring something other than the deck.
  // Charts are redrawn on every theme switch, so drop the last notice first.
  document.querySelectorAll("#results .runaway-notice").forEach((node) => node.remove());
  const runaways = runawayNotice(report);
  if (runaways) {
    runaways.classList.add("runaway-notice");
    headline.parentNode.insertBefore(runaways, headline.nextSibling);
  }
  const loops = loopNotice(report);
  if (loops) {
    // Same class, so the theme-switch redraw removes it along with the other.
    loops.classList.add("runaway-notice");
    headline.parentNode.insertBefore(loops, headline.nextSibling);
  }

  document.getElementById("inert").textContent =
    report.unparsed_cards.length ? report.unparsed_cards.join("\n") : "none";

  drawCurves();
  drawCommander();
  drawImpact();
  drawRemoval();
}

/* Chart styling, read from the theme each time it is used - hence getters -
 * so a chart drawn after a theme switch matches it. Transparent backgrounds
 * let the panel behind the chart (and the deck theme's blur) show through. */
const DARK = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  get font() { return { color: themeColor("--dim", "#8b93a3"), size: 11 }; },
  margin: { l: 48, r: 16, t: 40, b: 40 },
  legend: { orientation: "h", y: -0.18 },
  get colorway() {
    return [themeColor("--accent", "#e8b04a"), themeColor("--good", "#4cc38a"),
            themeColor("--bad", "#ef6461"), themeColor("--dim", "#8fa1bd")];
  },
  get xaxis() { const c = themeColor("--line", "#2b2f39"); return { gridcolor: c, zerolinecolor: c }; },
  get yaxis() { const c = themeColor("--line", "#2b2f39"); return { gridcolor: c, zerolinecolor: c }; },
};

function layout(title) {
  return Object.assign({}, DARK, { title: { text: title, font: { size: 13 } } });
}

/* Mana available against turn against lands in play, for the goldfished
 * player.
 *
 * These lines are AVERAGES, and that governs what a click can honestly do.
 * There may be no game at all in which you had 14 mana on turn 4 - that is
 * what averaging does - so "open the game where that happened" is a promise
 * the data cannot keep. Each point instead carries the game *closest* to the
 * average, and the hover says so, including what that game actually had. */
function drawCurves() {
  const wanted = ["mana_available", "lands", "cards_in_hand", "board_power"];
  const traces = report.series
    .filter((s) => s.player === 0 && wanted.includes(s.metric))
    .map((s) => ({
      x: s.turns,
      y: s.values,
      name: s.metric.replace(/_/g, " "),
      mode: "lines+markers",
      customdata: s.turns.map((_, i) => [
        s.representative ? s.representative[i] : -1,
        s.representative_value ? s.representative_value[i] : 0,
        s.samples[i],
      ]),
      hovertemplate:
        "average %{y:.1f} on turn %{x}, over %{customdata[2]} games"
        + "<br>closest game had %{customdata[1]}"
        + "<br><i>click to open that game</i><extra>%{fullData.name}</extra>",
    }));

  const node = document.getElementById("chart-curve");
  Plotly.newPlot(node, traces, layout("Your development, per your own turn (average)"), {
    displayModeBar: false, responsive: true,
  });
  node.on("plotly_click", (data) => {
    const index = data.points[0].customdata[0];
    if (index >= 0) openReplay(index);
  });
}

/* Unlike the curves above, a bar here *is* a set of games - "the games where
 * the commander landed on turn four" - so clicking one opens a game that
 * genuinely did that, with no qualification needed. */
function drawCommander() {
  const turns = Object.keys(report.commander_landed).map(Number).sort((a, b) => a - b);
  const node = document.getElementById("chart-commander");

  if (!turns.length) {
    node.innerHTML =
      '<p class="hint" style="padding:16px">The commander was never cast in '
      + "any game, so there is nothing to plot. That is usually a mana base "
      + "problem, or a commander whose cost the parser could not read.</p>";
    return;
  }

  const games = report.commander_landed_games || {};
  const trace = {
    x: turns,
    y: turns.map((t) => report.commander_landed[String(t)]),
    type: "bar",
    marker: { color: themeColor("--accent", "#e8b04a") },
    customdata: turns.map((t) => (games[String(t)] || [])[0] ?? -1),
    hovertemplate:
      "landed on turn %{x} in %{y} games<br><i>click to open one</i><extra></extra>",
  };
  Plotly.newPlot(
    node,
    [trace],
    Object.assign(layout(`Commander cast on your turn N (never: ${report.commander_never})`), {
      // Whole turns only. A bar chart over turns with a continuous axis draws
      // ticks at 1.5 and 2.5, which is where "turn 2.5" came from.
      xaxis: Object.assign({}, DARK.xaxis, { dtick: 1, title: { text: "turn" } }),
      yaxis: Object.assign({}, DARK.yaxis, { title: { text: "games" } }),
    }),
    { displayModeBar: false, responsive: true }
  );
  node.on("plotly_click", (data) => {
    const index = data.points[0].customdata;
    if (index >= 0) openReplay(index);
  });
}

function drawImpact() {
  const ranked = report.cards
    .filter((c) => c.reliable)
    .sort((a, b) => b.impact - a.impact)
    .slice(0, 18);

  const node = document.getElementById("chart-impact");
  if (!ranked.length) {
    node.innerHTML =
      '<p class="hint" style="padding:16px">Nothing ranked yet. Impact compares '
      + "games where a card was drawn against games where it was not, so a card "
      + "that appears in every game has nothing to compare against.</p>";
    return;
  }

  const trace = {
    x: ranked.map((c) => c.impact * 100),
    y: ranked.map((c) => c.name),
    type: "bar",
    orientation: "h",
    marker: { color: ranked.map((c) => (c.impact >= 0
      ? themeColor("--good", "#4cc38a") : themeColor("--bad", "#ef6461"))) },
    error_x: { type: "data", array: ranked.map((c) => c.margin * 100), color: themeColor("--dim", "#8b93a3") },
    hovertemplate: "%{y}: %{x:.1f}%<extra></extra>",
  };
  const l = layout("Win rate when drawn, minus when not (95% interval)");
  l.margin = { l: 170, r: 16, t: 40, b: 40 };
  l.yaxis = Object.assign({}, DARK.yaxis, { automargin: true });
  Plotly.newPlot(node, [trace], l, { displayModeBar: false, responsive: true });
}

function drawRemoval() {
  const node = document.getElementById("chart-removal");
  if (!report.removal.length) {
    node.innerHTML =
      '<p class="hint" style="padding:16px">Nothing was taken from you by an '
      + "identifiable culprit. Creatures traded in combat are not counted - "
      + "there is no single card to blame.</p>";
    return;
  }
  const top = report.removal.slice(0, 12);
  const trace = {
    x: top.map((t) => t.losses),
    y: top.map((t) => t.victim),
    type: "bar",
    orientation: "h",
    marker: { color: themeColor("--warn", "#f2c14e") },
    customdata: top.map((t) => Object.entries(t.by_source)
      .sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([name, n]) => `${name} (${n})`).join(", ")),
    hovertemplate: "%{y}: lost %{x}<br>mostly to %{customdata}<extra></extra>",
  };
  const l = layout("What you lose presence to");
  l.margin = { l: 170, r: 16, t: 40, b: 40 };
  l.yaxis = Object.assign({}, DARK.yaxis, { automargin: true });
  Plotly.newPlot(node, [trace], l, { displayModeBar: false, responsive: true });
}

/* ----------------------------------------------------------------- replay */

/* A replay is one game, played again from its seed, and the only place in the
 * app where a person reads what the engine actually did. Three things it has
 * to get right, and used not to:
 *
 * Turns are numbered per player. The engine's counter is global - every
 * player's turn advances it - so a four-player game's fourth round is turn
 * thirteen. Headings here say "Opponent 2, their turn 4", with the global
 * number kept alongside for anyone matching against a digest.
 *
 * Nothing is dropped silently. This view previously hid two whole kinds of
 * entry, one of which was `info` - the *default* kind, which carries "all
 * targets illegal; spell is countered". The one line explaining a game was the
 * line being thrown away. Now the detail level is a control, it is named, and
 * only the raw event stream is off by default.
 *
 * The shape of a turn is visible before you read it. Each turn in the sidebar
 * shows whose it was, what was cast and played, and where their life and board
 * ended up, so a fifty-turn game can be scanned rather than read. */

let replayView = null;
let replayDetail = "normal";

async function openReplay(index) {
  document.getElementById("replayindex").value = index;
  document.querySelector('nav button[data-tab="replay"]').click();
  await loadReplay();
}

document.getElementById("loadreplay").addEventListener("click", loadReplay);

document.querySelectorAll("#replaydetail button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#replaydetail button")
      .forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    replayDetail = button.dataset.detail;
    drawReplayLog();
  });
});

// Debounced: redrawing a long game's log on every keystroke is the one thing
// in this view that is slow enough to feel.
let replayFilterTimer = null;
document.getElementById("replaysearch").addEventListener("input", () => {
  clearTimeout(replayFilterTimer);
  replayFilterTimer = setTimeout(drawReplayLog, 140);
});

async function loadReplay() {
  const index = Number(document.getElementById("replayindex").value) || 0;
  setStatus(`replaying game ${index}...`);
  const view = await call("replay", index);
  if (view.error) return;

  replayView = view;
  drawReplayHeader();
  drawReplayTurns();
  drawReplayLog();
  // The board reads the same replay and fetches its pictures separately.
  Board.reset(view, followLogTo);
  document.getElementById("replayboard").textContent = view.final_board;
  setStatus(`game ${index} replayed`);
}

/* ------------------------------------------------------- board and log sync */

/* Two views of one game, and moving either should move the other - a board
 * with no idea what just happened is a screenshot, and a log with no idea what
 * the table looked like is what this replaced. */

document.getElementById("boardscrub").addEventListener("input", (event) => {
  Board.show(Number(event.target.value));
});
document.getElementById("boardscrub").addEventListener("change", (event) => {
  followLogTo(Board.frames[Number(event.target.value)]);
});
document.getElementById("boardprev").addEventListener("click", () => Board.step(-1));
document.getElementById("boardnext").addEventListener("click", () => Board.step(1));

/* Put the log where the board is. Highlighted rather than scrolled when the
 * line is already on screen, because a log that jumps under the cursor every
 * time the board ticks is worse than one that does not move. */
function followLogTo(frame) {
  if (!document.getElementById("boardfollow").checked) return;
  if (frame === undefined || frame === null) return;
  const lines = document.querySelectorAll("#replaylog .line[data-frame]");
  let best = null;
  lines.forEach((line) => {
    if (Number(line.dataset.frame) <= frame) best = line;
  });
  document.querySelectorAll("#replaylog .line.at").forEach((l) => l.classList.remove("at"));
  if (!best) return;
  best.classList.add("at");
  const box = document.getElementById("replaylog").getBoundingClientRect();
  const where = best.getBoundingClientRect();
  if (where.top < box.top || where.bottom > box.bottom) {
    best.scrollIntoView({ block: "center" });
  }
}

/* Who was at the table, how many turns each of them took, and how it ended.
 * Seat numbers alone ("winner: P2") mean nothing without the rest. */
function drawReplayHeader() {
  const host = document.getElementById("replayheader");
  host.innerHTML = "";
  const view = replayView;

  const summary = el("div", "replay-summary");
  summary.appendChild(el("span", "replay-outcome", view.outcome || ""));
  const played = view.turns_taken.filter((t) => !t.is_setup).length;
  summary.appendChild(el("span", "dim",
    `seed ${view.seed} · ${view.turns} player-turns`
    + (played === view.turns ? "" : ` · ${played} logged`)));
  host.appendChild(summary);

  const seats = el("div", "replay-seats");
  (view.seats || []).forEach((seat) => {
    const node = el("div", "seat seat-" + seat.id + (seat.won ? " won" : ""));
    node.appendChild(el("span", "seatname", seat.name));
    const fate = seat.won
      ? "won"
      : (seat.loss_reason
        ? `out on turn ${seat.left_on_turn} · ${seat.loss_reason.toLowerCase().replace(/_/g, " ")}`
        : "still in at the end");
    node.appendChild(el("span", "seatfate", `${seat.turns_taken} turns · ${fate}`));
    seats.appendChild(node);
  });
  host.appendChild(seats);
}

/* The table of contents. One row per turn actually taken, labelled by the
 * player whose turn it was and by their own count of it. */
function drawReplayTurns() {
  const host = document.getElementById("replayturns");
  host.innerHTML = "";

  replayView.turns_taken.forEach((turn, position) => {
    const row = el("button", "turnrow seat-" + turn.player);
    row.appendChild(el("span", "turnlabel", turn.label));
    // Setup is not a turn, so it has no number worth showing.
    if (!turn.is_setup) {
      row.appendChild(el("span", "turnglobal", `game turn ${turn.turn}`));
    }

    const did = [];
    if (turn.spells_cast) did.push(`${turn.spells_cast} cast`);
    if (turn.lands_played) did.push(`${turn.lands_played} land${turn.lands_played > 1 ? "s" : ""}`);
    if (turn.life !== null && turn.life !== undefined) {
      did.push(`${turn.life} life`);
      did.push(`${turn.permanents} permanents`);
    }
    if (did.length) row.appendChild(el("span", "turndid", did.join(" · ")));

    row.addEventListener("click", () => {
      document.querySelectorAll("#replayturns .turnrow")
        .forEach((b) => b.classList.remove("chosen"));
      row.classList.add("chosen");
      const header = document.getElementById(`turnhead-${position}`);
      if (header) header.scrollIntoView({ block: "start", behavior: "smooth" });
      Board.showAtFrame(turn.start);
    });
    host.appendChild(row);
  });
}

function replayFilter() {
  return document.getElementById("replaysearch").value.trim().toLowerCase();
}

/* Kinds the server told us are key or noise, so the page does not keep its own
 * second copy of that list to drift from. */
function shownAtDetail(kind) {
  const view = replayView;
  if (replayDetail === "everything") return true;
  if ((view.noise_kinds || []).includes(kind)) return false;
  if (replayDetail === "key") return (view.key_kinds || []).includes(kind);
  return true;
}

function drawReplayLog() {
  const host = document.getElementById("replaylog");
  host.innerHTML = "";
  if (!replayView) return;

  const needle = replayFilter();
  const frames = replayView.frames;
  let shown = 0;

  replayView.turns_taken.forEach((turn, position) => {
    const lines = [];
    for (let i = turn.start; i < turn.end; i += 1) {
      const frame = frames[i];
      // The turn's own banner is already the heading above it; repeating it as
      // the first line of every turn is pure noise.
      if (frame.kind === "turn") continue;
      if (!shownAtDetail(frame.kind)) continue;
      if (needle && !frame.text.toLowerCase().includes(needle)) continue;
      // The position carries the frame's index, which is what the board is
      // keyed on: the frames array is dense, so a line's place in it is its
      // place in the game.
      lines.push({ frame, at: i });
    }
    // While filtering, a turn where nothing matched is not worth a heading.
    if (needle && !lines.length) return;

    const block = el("div", "turnblock seat-" + turn.player);
    const header = el("div", "turnhead");
    header.id = `turnhead-${position}`;
    header.appendChild(el("span", "turnlabel", turn.label));
    if (!turn.is_setup) {
      header.appendChild(el("span", "turnglobal", `game turn ${turn.turn}`));
    }
    block.appendChild(header);

    if (!lines.length) {
      block.appendChild(el("div", "line dim", "nothing logged at this detail level"));
    }

    let step = null;
    lines.forEach(({ frame, at }) => {
      if (frame.step !== step) {
        step = frame.step;
        block.appendChild(el("div", "stephead", step.toLowerCase().replace(/_/g, " ")));
      }
      const line = el("div", `line kind-${frame.kind}`);
      line.style.paddingLeft = `${12 + frame.depth * 16}px`;
      line.dataset.frame = at;
      line.appendChild(el("span", "linekind", frame.kind));
      line.appendChild(el("span", "linetext", frame.text));
      // Reading a line and wanting to see the table it happened on is the
      // whole reason both views are here.
      line.addEventListener("click", () => {
        document.querySelectorAll("#replaylog .line.at").forEach((l) => l.classList.remove("at"));
        line.classList.add("at");
        Board.showAtFrame(at);
      });
      block.appendChild(line);
      shown += 1;
    });

    if (turn.life !== null && turn.life !== undefined) {
      block.appendChild(el("div", "turnfoot",
        `end of turn: ${turn.life} life · ${turn.lands} lands · `
        + `${turn.cards_in_hand} in hand · ${turn.permanents} permanents`
        + ` · ${turn.board_power} power on board`));
    }
    host.appendChild(block);
  });

  if (!shown) {
    host.appendChild(el("div", "empty-note", needle
      ? `Nothing in this game matches "${needle}".`
      : "Nothing to show at this detail level."));
  }
}

/* ---------------------------------------------------------------- sandbox */

const searchBox = document.getElementById("cardsearch");
let searchTimer = null;

searchBox.addEventListener("input", () => {
  clearTimeout(searchTimer);
  // Debounced: every keystroke is a database query, and the operator is
  // usually still typing.
  searchTimer = setTimeout(runSearch, 160);
});

async function runSearch() {
  const text = searchBox.value.trim();
  const list = document.getElementById("searchresults");
  list.innerHTML = "";
  if (text.length < 2) return;

  const results = await call("sandbox_search", text);
  if (!Array.isArray(results)) return;
  results.forEach((card) => {
    const item = el("li");
    item.innerHTML =
      `<span class="cost">${card.mana_cost || ""}</span>${card.name}`
      + `<span class="type">${card.type_line}</span>`;
    item.addEventListener("click", () => chooseCard(card.name, item));
    list.appendChild(item);
  });
}

async function chooseCard(name, item) {
  chosenCard = name;
  document.querySelectorAll("#searchresults li").forEach((li) => li.classList.remove("chosen"));
  if (item) item.classList.add("chosen");

  renderCard(await call("sandbox_inspect", name));
}

// Reading completely and reading correctly are different questions. The badge
// answers the first; the side-by-side below answers the second, and only a
// person can.
function renderCard(info) {
  const host = document.getElementById("cardinfo");
  host.innerHTML = "";
  if (info.error) { host.textContent = info.error; return; }

  host.appendChild(el("div", "verdict " + (info.fully_parsed ? "ok" : "fail"),
    info.fully_parsed ? "the parser read this card completely"
                      : "the parser could NOT read all of this card"));

  info.faces.forEach((face) => {
    if (info.faces.length > 1) host.appendChild(el("h3", null, face.name));

    face.abilities.forEach((ability) => {
      const block = el("div", "reading " + (ability.unparsed ? "fail" : "ok"));
      const said = el("div", "said");
      said.appendChild(el("span", "label", "card says"));
      said.appendChild(el("span", "text", ability.text || ability.kind));
      block.appendChild(said);

      const read = el("div", "read");
      read.appendChild(el("span", "label", "engine will"));
      read.appendChild(el("span", "text", ability.understood));
      block.appendChild(read);

      block.appendChild(el("div", "kindtag",
        `${ability.kind}${ability.effects.length ? ": " + ability.effects.join(", ") : ""}`));
      host.appendChild(block);
    });

    face.failures.forEach((failure) => {
      host.appendChild(el("div", "reading fail",
        `unread: ${failure.reason} at "${failure.stopped_at}" - ${failure.remaining}`));
    });
  });

  host.appendChild(reviewControls(info));
}

function reviewControls(info) {
  const box = el("div", "review");
  const state = el("div", "reviewstate " + info.verdict);

  if (info.suppressed) {
    // Say this loudly and separately from "the parser could not read it".
    // On a card that was already fully unparsed, switching it off changes
    // nothing visible, and the control looks broken when it is not.
    state.textContent =
      "SWITCHED OFF by you - this card's abilities are withheld from the "
      + "engine, in the sandbox and in real runs"
      + (info.note ? ` - "${info.note}"` : "");
  } else if (info.verdict === "unreviewed") {
    state.textContent = "nobody has checked this card";
  } else if (!info.verdict_current) {
    // The grammar has moved since the verdict was given, so it describes a
    // parse that no longer exists.
    state.textContent = `marked ${info.verdict}, but the parse has CHANGED since - check it again`;
  } else {
    state.textContent = `marked ${info.verdict}`
      + (info.note ? ` - "${info.note}"` : "");
  }
  box.appendChild(state);

  const note = el("input", "reviewnote");
  note.placeholder = "what is wrong with it (optional)";
  note.value = info.note || "";
  box.appendChild(note);

  const row = el("div", "reviewbuttons");
  [["approved", "matches the card"],
   ["inert", "wrong - switch it off"],
   ["unreviewed", "clear"]].forEach(([verdict, label]) => {
    const button = el("button", "verdict-" + verdict, label);
    // Approving a card the parser could not read means nothing: there is no
    // behaviour to vouch for. Offering the button anyway invites a verdict
    // that records agreement with a blank.
    if (verdict === "approved" && !info.fully_parsed) {
      button.disabled = true;
      button.title = "nothing to approve - the parser could not read this card";
    }
    button.addEventListener("click", async () => {
      renderCard(await call("sandbox_judge", info.name, verdict, note.value));
      setStatus(`${info.name}: ${verdict}`);
    });
    row.appendChild(button);
  });
  box.appendChild(row);
  return box;
}

document.getElementById("putcard").addEventListener("click", async () => {
  if (!chosenCard) { setStatus("pick a card first", true); return; }
  const zone = document.getElementById("putzone").value;
  const player = Number(document.getElementById("putplayer").value);
  const count = Math.max(1, Number(document.getElementById("putcount").value) || 1);
  applyState(await call("sandbox_put", chosenCard, zone, player, count));
});

const SANDBOX_BUTTONS = {
  "sb-reset": ["sandbox_reset"],
  "sb-settle": ["sandbox_settle"],
  "sb-resolve": ["sandbox_resolve"],
  "sb-advance": ["sandbox_advance"],
  "sb-turn": ["sandbox_next_turn"],
};

Object.entries(SANDBOX_BUTTONS).forEach(([id, [method]]) => {
  document.getElementById(id).addEventListener("click", async () => {
    applyState(await call(method));
  });
});

document.getElementById("sb-mana").addEventListener("click", async () => {
  applyState(await call("sandbox_give_mana", 10, 0));
});

async function refreshSandbox() {
  applyState(await call("sandbox_state"));
}

function applyState(state) {
  if (!state || state.error) {
    if (state && state.error) setStatus(state.error, true);
    if (!state) return;
  }
  sandboxState = state;
  pendingAction = null;
  document.getElementById("sb-targets").innerHTML = "";

  document.getElementById("sb-phase").textContent =
    `Turn ${state.turn} · ${state.phase.toLowerCase()} / ${state.step.toLowerCase()}`
    + ` · active: ${state.players[state.active_player].name}`
    + (state.message ? ` · ${state.message}` : "");

  /* A card can still say "you win the game", and nothing responds after that.
   * A board that has stopped for that reason has to say so, or it reads as a
   * sandbox that has broken. */
  const over = document.getElementById("sb-over");
  over.hidden = !state.game_over;
  if (state.game_over) {
    const who = (state.winners || [])
      .map((id) => (state.players[id] || {}).name || `P${id}`).join(", ");
    over.textContent = (who ? `${who} won. ` : "The game has ended. ")
      + "Nothing else will happen on this board — reset to carry on.";
  }

  renderBenchRules(state);

  const board = document.getElementById("sb-board");
  board.innerHTML = "";
  state.players.forEach((player) => {
    const zone = el("div", "zone");
    const out = player.has_lost
      ? ` — OUT (${player.loss_reason.toLowerCase().replace(/_/g, " ")})` : "";
    zone.appendChild(el("div", "zonename" + (player.has_lost ? " lost" : ""),
      `${player.name} — ${player.life} life, ${player.mana} mana in pool, `
      + `${player.library} in library`
      + (player.poison ? `, ${player.poison} poison` : "") + out));
    ["battlefield", "hand", "graveyard"].forEach((where) => {
      if (!player[where].length) return;
      const row = el("div");
      row.appendChild(el("div", "zonename", where));
      player[where].forEach((obj) => row.appendChild(renderPermanent(obj)));
      zone.appendChild(row);
    });
    board.appendChild(zone);
  });

  const stack = document.getElementById("sb-stack");
  stack.innerHTML = "";
  if (!state.stack.length) stack.appendChild(el("div", "zonename", "empty"));
  state.stack.forEach((obj) => stack.appendChild(renderPermanent(obj)));

  // Already windowed to the entries worth reading; the raw event stream is
  // dropped on the Python side, where the whole log is, rather than here,
  // where only the tail of it ever arrives.
  document.getElementById("sb-log").textContent =
    state.log.map((e) => `${"  ".repeat(e.depth)}[${e.kind}] ${e.text}`).join("\n");

  refreshActions();
}

/* The rules this bench has switched off, as checkboxes.
 *
 * Built from what the server says exists rather than from a list here: the
 * switches are defined next to the code that honours them, and a page holding
 * its own copy would quietly stop matching. */
function renderBenchRules(state) {
  const host = document.getElementById("sb-rules");
  if (!state.rules) { host.innerHTML = ""; return; }
  host.innerHTML = "";

  Object.entries(state.rules).forEach(([name, on]) => {
    const row = el("label", "benchrule" + (on ? " off" : ""));
    const box = el("input");
    box.type = "checkbox";
    // Checked means the rule is *suspended*, which is what the heading above
    // the panel says these are. Labelling each one by the rule it switches off
    // and then checking it for "enforced" would invert every reading.
    box.checked = !!on;
    box.addEventListener("change", async () => {
      applyState(await call("sandbox_set_rule", name, box.checked));
    });
    row.appendChild(box);
    const text = el("span", "benchtext");
    text.appendChild(el("span", "benchname", name.replace(/_/g, " ")));
    text.appendChild(el("span", "benchwhy",
      (state.rule_descriptions || {})[name] || ""));
    row.appendChild(text);
    host.appendChild(row);
  });
}

function renderPermanent(obj) {
  const node = el("div", "perm"
    + (obj.tapped ? " tapped" : "")
    + (obj.unreadable ? " unreadable" : ""));
  node.textContent = obj.name;

  if (obj.power !== null && obj.power !== undefined) {
    node.appendChild(el("span", "pt", `${obj.power}/${obj.toughness}`));
  }
  if (obj.damage) node.appendChild(el("span", "pt", ` ${obj.damage} dmg`));
  const counters = Object.entries(obj.counters || {});
  if (counters.length) {
    node.appendChild(el("span", "pt",
      " " + counters.map(([k, v]) => `${v}×${k}`).join(" ")));
  }
  if (obj.keywords && obj.keywords.length) {
    node.appendChild(el("span", "kw", obj.keywords.join(" ")));
  }
  node.title = (obj.abilities || []).map((a) => (a.unparsed ? "UNREAD: " : "") + a.text).join("\n")
    || obj.type_line;
  return node;
}

async function refreshActions() {
  const host = document.getElementById("sb-actions");
  host.innerHTML = "";
  const actions = await call("sandbox_legal", 0);
  if (!Array.isArray(actions) || !actions.length) {
    host.appendChild(el("div", "zonename",
      "nothing you can do right now — try adding mana, or advancing the step"));
    return;
  }
  actions.forEach((action) => {
    const button = el("button", null, action.description);
    button.addEventListener("click", () => beginAction(action));
    host.appendChild(button);
  });
}

/* Targets are chosen by the operator, never by the engine's default. The
 * default picks the first legal candidate, which for a Lightning Bolt is as
 * likely to be your own creature as theirs. */
async function beginAction(action) {
  const groups = await call("sandbox_targets", action.index, 0);
  const host = document.getElementById("sb-targets");
  host.innerHTML = "";

  if (!Array.isArray(groups) || !groups.length) {
    applyState(await call("sandbox_perform", action.index, 0, ""));
    return;
  }

  pendingAction = { action, groups, chosen: groups.map(() => []) };
  groups.forEach((group, index) => {
    const box = el("div", "group");
    box.appendChild(el("div", "what",
      `${group.description}${group.optional ? " (optional)" : ""}`));
    group.candidates.forEach((candidate) => {
      const button = el("button", null,
        `${candidate.name} (${candidate.controller === 0 ? "yours" : "theirs"})`);
      button.addEventListener("click", () => {
        pendingAction.chosen[index] = [candidate.id];
        box.querySelectorAll("button").forEach((b) => b.classList.remove("primary"));
        button.classList.add("primary");
      });
      box.appendChild(button);
    });
    host.appendChild(box);
  });

  const go = el("button", "primary", "Do it");
  go.addEventListener("click", async () => {
    const chosen = pendingAction.chosen;
    applyState(await call("sandbox_perform", action.index, 0, JSON.stringify(chosen)));
  });
  host.appendChild(go);
}

/* -------------------------------------------------------------- swap lab */

/* Trying a card out. The screen is a list of *slots*: a card in the deck, and
 * the things that might replace it. What that means arithmetically depends on
 * one checkbox, and the two readings answer different questions:
 *
 *   together  - the swaps apply at once, so slots multiply. Ten one-candidate
 *               slots is one deck with ten changes: "is this list better".
 *   separate  - each candidate is tried alone, so slots add. Five slots is
 *               five decks each differing by one card: "which swap is worth
 *               making". The combined run cannot answer that - if it wins,
 *               you still do not know which of the changes did it.
 *
 * Candidates *within* one slot are always alternatives, because they all
 * replace the same card. Five candidates in one slot is five decks either way.
 */

let labCards = [];      // every card name in deck 1
let labSlots = [];      // [{ original, candidates: [] }]
let labRows = null;     // the last comparison, for the "open" buttons

function labModeHint() {
  const combine = document.getElementById("labcombine").checked;
  document.getElementById("labmodehint").textContent = combine
    ? "swaps apply together - one deck carrying every change"
    : "each swap tried on its own - one deck per change";
}

async function labLoadCards() {
  const text = document.getElementById("deck0").value.trim();
  if (!text) { setStatus("paste a decklist into the first slot first", true); return; }
  setStatus("reading deck...");
  const payload = await call("lab_cards", text);
  if (payload.error) return;
  labCards = payload.cards || [];
  setStatus(labCards.length + " cards to choose from");
  if (!labSlots.length) labSlots.push({ original: labCards[0] || "", candidates: [] });
  drawLabSlots();
  labRefreshPlan();
}

function drawLabSlots() {
  const host = document.getElementById("labslots");
  host.textContent = "";

  labSlots.forEach((slot, index) => {
    const box = el("div", "labslot");
    const head = el("div", "head");

    /* A datalist rather than a select: a hundred-card deck is too many to
     * scroll, and the person already knows the name they are looking for. */
    const picker = el("input");
    picker.setAttribute("list", "labcardlist");
    picker.placeholder = "card in your deck";
    picker.value = slot.original;
    picker.addEventListener("change", () => {
      slot.original = picker.value.trim();
      labRefreshPlan();
    });
    head.appendChild(el("span", "hint", "Replace"));
    head.appendChild(picker);

    const candidate = el("input");
    candidate.placeholder = "with... (any card name, then Enter)";
    candidate.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const name = candidate.value.trim();
      if (!name) return;
      slot.candidates.push(name);
      candidate.value = "";
      drawLabSlots();
      labRefreshPlan();
    });
    head.appendChild(el("span", "hint", "with"));
    head.appendChild(candidate);

    const drop = el("button", null, "Remove slot");
    drop.addEventListener("click", () => {
      labSlots.splice(index, 1);
      drawLabSlots();
      labRefreshPlan();
    });
    head.appendChild(drop);
    box.appendChild(head);

    const chips = el("div", "candidates");
    slot.candidates.forEach((name, position) => {
      const chip = el("span", "chip", name);
      const kill = el("button", null, "×");
      kill.title = "stop trying " + name;
      kill.addEventListener("click", () => {
        slot.candidates.splice(position, 1);
        drawLabSlots();
        labRefreshPlan();
      });
      chip.appendChild(kill);
      chips.appendChild(chip);
    });
    if (!slot.candidates.length) {
      chips.appendChild(el("span", "hint", "type a card name and press Enter"));
    }
    box.appendChild(chips);
    host.appendChild(box);
  });

  let list = document.getElementById("labcardlist");
  if (!list) {
    list = el("datalist");
    list.id = "labcardlist";
    document.body.appendChild(list);
  }
  list.textContent = "";
  labCards.forEach((name) => {
    const option = el("option");
    option.value = name;
    list.appendChild(option);
  });
}

/* The variant count, before the button is pressed. Combined mode multiplies,
 * so four slots with three candidates each is eighty-one decks rather than the
 * twelve it looks like - and a progress bar that never finishes is a bad way
 * to discover that. */
async function labRefreshPlan() {
  const text = document.getElementById("deck0").value.trim();
  const note = document.getElementById("labplan");
  if (!text) { note.textContent = ""; return; }

  const combine = document.getElementById("labcombine").checked;
  const payload = await call("lab_plan", text, JSON.stringify(labSlots), combine);
  if (payload.error) { note.textContent = ""; return; }

  const count = (payload.variants || []).length;
  const games = Number(document.getElementById("games").value) || 100;
  note.textContent = count < 2
    ? "add a replacement to compare against"
    : count + " decks (baseline + " + (count - 1) + ") x " + games
      + " games each = " + (count * games) + " games "
      + "(game count and seed come from the Decks tab)";

  document.getElementById("labproblems").textContent =
    (payload.problems || []).join("\n");
}

document.getElementById("labload").addEventListener("click", labLoadCards);
document.getElementById("labadd").addEventListener("click", () => {
  labSlots.push({ original: labCards[0] || "", candidates: [] });
  drawLabSlots();
  labRefreshPlan();
});
document.getElementById("labcombine").addEventListener("change", () => {
  labModeHint();
  labRefreshPlan();
});

document.getElementById("labrun").addEventListener("click", async () => {
  const decks = [];
  for (let i = 0; i < 4; i += 1) {
    const text = document.getElementById("deck" + i).value.trim();
    if (text) decks.push(text);
  }
  if (!decks.length) { setStatus("paste a decklist first", true); return; }

  const games = Number(document.getElementById("games").value) || 100;
  const seed = Number(document.getElementById("seed").value) || 0;
  const combine = document.getElementById("labcombine").checked;

  const button = document.getElementById("labrun");
  button.disabled = true;
  setStatus("running variants...");

  const started = await call(
    "start_lab", JSON.stringify(decks), JSON.stringify(labSlots), combine, games, seed
  );
  if (started.error) {
    button.disabled = false;
    setStatus(started.error, true);
    return;
  }
  showStop(true);
  setStatus("running " + started.variants + " decks of " + games + " games each...");
});

function onLabFinished(payload) {
  const button = document.getElementById("labrun");
  button.disabled = false;
  const bar = document.getElementById("progress");
  if (bar) bar.style.display = "none";
  showStop(false);
  if (payload.cancelled) { setStatus("comparison stopped - no results were kept"); return; }
  if (payload.error) { setStatus(payload.error, true); return; }

  labRows = payload.rows || [];
  drawLabResults(payload);
  setStatus("compared " + labRows.length + " decks on seed " + payload.seed);
}

/* Games the engine stopped because something looped. Shown as loudly as a
 * headline number, because it means a card is wrong rather than a deck is
 * slow - and because the alternative is a win rate quietly computed over
 * games that never finished. */
/* Loops the engine noticed and skipped to the end of (CR 732.2a). Not an
 * error - an infinite that ends a game is the deck working - but worth seeing,
 * because a loop that was *stopped* for changing nothing is usually a card
 * being read wrong. */
function loopNotice(payload) {
  const loops = payload.loops || {};
  const names = Object.keys(loops);
  const draws = payload.loop_draws || 0;
  if (!names.length && !draws) return null;
  const box = el("div", "panel");
  box.appendChild(el("strong", null, "Infinite loops"));
  box.appendChild(el("div", "hint",
    "After eight identical cycles the engine skips to the result - as far as "
    + "the loop can kill, never far enough to kill whoever runs it, and never "
    + "past what it has to spend. A mandatory loop nothing can stop is a draw."));
  if (draws) {
    box.appendChild(el("div", null,
      draws + " game" + (draws === 1 ? "" : "s") + " drawn on a mandatory loop"));
  }
  names.forEach((name) => {
    box.appendChild(el("div", null, loops[name] + " x  " + name));
  });
  return box;
}

function runawayNotice(payload) {
  const runaways = payload.runaways || {};
  const names = Object.keys(runaways);
  if (!names.length) return null;
  const total = names.reduce((sum, name) => sum + runaways[name], 0);
  const box = el("div", "panel bad");
  box.appendChild(el("strong", null,
    total + " game" + (total === 1 ? "" : "s") + " were stopped as runaways"));
  box.appendChild(el("div", "hint",
    "An ability repeated until the engine cut the game off. That is a bug in "
    + "how the card was read - almost always a cost the engine could not "
    + "charge - not a fact about the deck. These games are excluded from "
    + "nothing, so treat the numbers below with suspicion until it is fixed."));
  names.forEach((name) => {
    box.appendChild(el("div", null, runaways[name] + " x  " + name));
  });
  return box;
}

function drawLabResults(payload) {
  const host = document.getElementById("labresults");
  host.textContent = "";
  document.getElementById("labproblems").textContent =
    (payload.problems || []).join("\n");
  if (!labRows || !labRows.length) return;

  const table = el("table");
  const head = el("thead");
  const headRow = el("tr");
  [
    "Deck", "Win rate", "vs baseline", "Avg win turn", "vs baseline",
    "Stall rate", "Commander turn", ""
  ].forEach((title) => headRow.appendChild(el("th", null, title)));
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  labRows.forEach((row, index) => {
    const tr = el("tr", index === 0 ? "baseline" : null);

    const name = el("td");
    name.appendChild(el("div", null, row.label));
    if (row.swaps && row.swaps.length > 1) {
      name.appendChild(el("div", "hint", row.swaps.join(", ")));
    }
    tr.appendChild(name);

    if (row.error) {
      const cell = el("td", "bad", row.error);
      cell.colSpan = 7;
      tr.appendChild(cell);
      body.appendChild(tr);
      return;
    }

    tr.appendChild(el("td", null, pct(row.win_rate)));
    tr.appendChild(labDelta(row.delta_win_rate, pct, true));
    tr.appendChild(el("td", null, row.average_win_turn.toFixed(1)));
    // Winning *sooner* is better, so here the good sign is the negative one.
    tr.appendChild(labDelta(row.delta_win_turn, (v) => v.toFixed(2), false));
    tr.appendChild(el("td", null, pct(row.stall_rate)));
    tr.appendChild(el("td", null, row.commander_turn.toFixed(1)));

    const open = el("td");
    const button = el("button", "open", "Open");
    button.title = "show this variant on the Results screen";
    button.addEventListener("click", () => labOpen(index));
    open.appendChild(button);
    tr.appendChild(open);

    body.appendChild(tr);
  });
  table.appendChild(body);
  host.appendChild(table);

  host.appendChild(el("p", "hint",
    "Every deck played the same " + payload.games + " seeds against the same "
    + "opponents. Changing a card changes the library, so the draws still "
    + "diverge - but run-to-run variance is out of the comparison, which is "
    + "the part that was drowning the signal."));
}

function pct(value) {
  return (value * 100).toFixed(1) + "%";
}

/* A delta cell coloured by whether it is an improvement. ``higherIsBetter``
 * exists because winning sooner is a *smaller* number, and colouring that red
 * would say the opposite of what happened. */
function labDelta(value, format, higherIsBetter) {
  if (value === undefined || value === null) return el("td", "delta", "—");
  if (Math.abs(value) < 1e-9) return el("td", "delta", format(0));
  const better = higherIsBetter ? value > 0 : value < 0;
  const cell = el("td", "delta " + (better ? "good" : "bad"));
  cell.textContent = (value > 0 ? "+" : "") + format(value);
  return cell;
}

async function labOpen(index) {
  const payload = await call("lab_report", index);
  if (payload.error) return;
  report = payload;
  document.querySelector('nav button[data-tab="results"]').click();
  if (typeof drawResults === "function") drawResults();
}
