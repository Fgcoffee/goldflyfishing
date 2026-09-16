"use strict";

/* How the page reaches Python.
 *
 * Two transports behind one interface. Inside the desktop app the page talks
 * to the bridge over a QWebChannel; in a browser it talks to the same bridge
 * through mtgfish.web over HTTP, with signals arriving as server-sent events.
 * Nothing above this file knows which one it has.
 *
 *   Backend.ready            a promise, resolved once connected
 *   Backend.call(m, ...a)    the slot's raw JSON string reply
 *   Backend.on(signal, fn)   listen to a bridge signal
 *   Backend.has(m)           whether this backend has that slot at all
 *
 * ``has`` is what lets the page outlive the Python beside it. The folder is
 * edited continuously, so the page and the server can briefly disagree about
 * what exists; a feature whose slot is missing hides itself instead of
 * failing when clicked.
 */

const Backend = (() => {
  const listeners = {};
  let methods = new Set();
  let kind = null;
  let qtBridge = null;
  let loadedVersion = null;

  function emit(signal, args) {
    (listeners[signal] || []).forEach((fn) => {
      try { fn(...args); } catch (err) { console.error(err); }
    });
  }

  function on(signal, fn) {
    (listeners[signal] = listeners[signal] || []).push(fn);
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error(`could not load ${src}`));
      document.head.appendChild(tag);
    });
  }

  /* ---------------------------------------------------------------- qt */

  async function connectQt() {
    // Served by QtWebEngine itself; PySide6 ships no copy on disk.
    await loadScript("qrc:///qtwebchannel/qwebchannel.js");
    await new Promise((resolve) => {
      new QWebChannel(qt.webChannelTransport, (channel) => {
        qtBridge = channel.objects.bridge;
        Object.keys(qtBridge).forEach((name) => {
          const member = qtBridge[name];
          if (typeof member === "function" && !name.startsWith("_")) methods.add(name);
          else if (member && typeof member.connect === "function") {
            member.connect((...args) => emit(name, args));
          }
        });
        resolve();
      });
    });
  }

  /* -------------------------------------------------------------- http */

  async function connectHttp() {
    let info = null;
    // A server mid-restart refuses connections for a second or two; that is
    // not worth an error page.
    for (let attempt = 0; attempt < 20 && !info; attempt += 1) {
      try {
        const response = await fetch("api/methods", { cache: "no-store", credentials: "same-origin" });
        if (response.ok) info = await response.json();
      } catch (err) { /* retry */ }
      if (!info) await new Promise((r) => setTimeout(r, 750));
    }
    if (!info) throw new Error("the server is not answering");
    methods = new Set(info.methods);
    loadedVersion = info.version;
    openEvents();
    setInterval(checkVersion, 60000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkVersion();
    });
  }

  function openEvents() {
    const source = new EventSource("api/events");
    source.addEventListener("hello", (event) => {
      emit("connection", [true]);
      noticeVersion(JSON.parse(event.data).version);
    });
    source.onmessage = (event) => {
      const message = JSON.parse(event.data);
      emit(message.signal, message.args || []);
    };
    // EventSource reconnects by itself, resending the last event id, and the
    // server replays anything missed - so there is nothing to do but say so.
    source.onerror = () => emit("connection", [false]);
  }

  async function checkVersion() {
    try {
      const response = await fetch("api/version", { cache: "no-store" });
      if (response.ok) noticeVersion((await response.json()).version);
    } catch (err) { /* offline for a moment; the next check will tell */ }
  }

  function noticeVersion(version) {
    if (loadedVersion && version && version !== loadedVersion) emit("update-available", [version]);
  }

  /* -------------------------------------------------------------- both */

  const ready = (async () => {
    if (window.qt && window.qt.webChannelTransport) {
      kind = "qt";
      await connectQt();
    } else {
      kind = "http";
      await connectHttp();
    }
  })();

  function call(method, ...args) {
    if (!methods.has(method)) {
      return Promise.resolve(JSON.stringify({
        error: `this version of the app has no "${method}" yet`, missing: true,
      }));
    }
    if (kind === "qt") {
      return new Promise((resolve) => qtBridge[method](...args, (raw) => resolve(raw)));
    }
    return fetch(`api/call/${encodeURIComponent(method)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ args }),
    })
      .then(async (response) => (await response.text()) || JSON.stringify({ error: `HTTP ${response.status}` }))
      .catch((err) => JSON.stringify({ error: `could not reach the server (${err.message})` }));
  }

  return {
    ready,
    call,
    on,
    has: (method) => methods.has(method),
    get kind() { return kind; },
  };
})();
