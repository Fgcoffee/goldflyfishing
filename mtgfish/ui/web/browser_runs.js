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

  /* -- simulating in the browser (behind a flag) ---------------------------
   *
   * Off unless the page is opened with ?engine=browser, or the choice was
   * saved (localStorage "goldfly.engine" = "browser"). The live site keeps
   * the server engine until the auditor and the user give the go.
   */
  const ENGINE_KEY = "goldfly.engine";
  let runner = null;

  function enabled() {
    const asked = new URLSearchParams(location.search).get("engine");
    try {
      if (asked === "browser" || asked === "server") localStorage.setItem(ENGINE_KEY, asked);
      return localStorage.getItem(ENGINE_KEY) === "browser";
    } catch (err) {
      return asked === "browser";  // storage blocked: the URL alone decides
    }
  }

  /* Seconds one game may run before it's stopped and recorded as not
   * finished: ?gameTimeout=N, remembered in localStorage
   * "goldfly.gameTimeout". Default 60. */
  const TIMEOUT_KEY = "goldfly.gameTimeout";

  function gameTimeout() {
    const asked = Number(new URLSearchParams(location.search).get("gameTimeout"));
    try {
      if (asked > 0) localStorage.setItem(TIMEOUT_KEY, String(asked));
      const saved = Number(localStorage.getItem(TIMEOUT_KEY));
      return saved > 0 ? saved : 60;
    } catch (err) {
      return asked > 0 ? asked : 60;
    }
  }

  /* Play a simulation here. Resolves with the Results payload, as the
   * server's run_finished would carry it; Results and Replay then use it. */
  async function simulate(decklists, games, seed, hooks = {}, deckName = "") {
    const module = await engine();
    if (runner === null) runner = new module.BrowserRunner();
    close();
    const outcome = await runner.run(decklists, {
      games,
      seed,
      gameTimeoutSeconds: gameTimeout(),
      onProgress: hooks.onProgress,
      onStatus: hooks.onStatus,
      deckName,
    });
    if (outcome.stored !== null && !outcome.payload.error && !outcome.payload.cancelled) {
      current = {
        stored: outcome.stored,
        report: outcome.payload,
        replayer: new module.Replayer(outcome.stored),
      };
    }
    return outcome;
  }

  function cancel() {
    return runner !== null && runner.cancel();
  }

  return {
    handles,
    call,
    list,
    open,
    close,
    enabled,
    simulate,
    cancel,
    get current() { return current; },
  };
})();
