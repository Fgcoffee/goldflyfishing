"use strict";

/* Browser runs on the Results and Replay screens.
 *
 * A run played in the browser by the TypeScript engine is stored on the
 * server (/api/browser-runs) with its game records and what is needed to
 * replay it. Opened from History, its report is built from those records and
 * its games are replayed in a Web Worker by the same engine that played them
 * (engine/goldfly-site.js, built from webapp/src/site). Both answer in the
 * JSON Python's bridge sends, so the Results and Replay screens draw a
 * browser run with the code they already have.
 *
 * While a browser run is open, `call("report" | "replay" | "replay_board")`
 * comes here instead of going to Python (see `call` in app.js). Starting a
 * simulation or opening a server run closes it.
 */

const BrowserRuns = (() => {
  const HANDLED = new Set(["report", "replay", "replay_board"]);
  let site = null;     // the engine module, loaded on first use
  let current = null;  // {stored, report, replayer}

  async function engine() {
    if (site === null) {
      try {
        site = await import("./engine/goldfly-site.js");
      } catch (err) {
        throw new Error("the browser engine isn't built on this server (webapp: npm run build:site)");
      }
    }
    return site;
  }

  function handles(method) {
    return current !== null && HANDLED.has(method);
  }

  /* The raw JSON string reply, as Backend.call gives. */
  async function call(method, ...args) {
    let payload;
    try {
      if (method === "report") payload = current.report;
      else if (method === "replay") payload = await current.replayer.replay(Number(args[0]) || 0);
      else payload = await current.replayer.board(Number(args[0]) || 0);
    } catch (err) {
      payload = { error: String(err && err.message ? err.message : err) };
    }
    return JSON.stringify(payload);
  }

  async function list() {
    const response = await fetch("/api/browser-runs", { credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `the server said ${response.status}`);
    return data.runs || [];
  }

  /* Open a stored browser run: returns its report, and replays go to it. */
  async function open(id) {
    const response = await fetch(`/api/browser-runs/${encodeURIComponent(id)}`, { credentials: "same-origin" });
    const stored = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(stored.error || `the server said ${response.status}`);
    const module = await engine();
    close();
    current = { stored, report: module.resultsFor(stored), replayer: new module.Replayer(stored) };
    return current;
  }

  function close() {
    if (current !== null) current.replayer.terminate();
    current = null;
  }

  return {
    handles,
    call,
    list,
    open,
    close,
    get current() { return current; },
  };
})();
