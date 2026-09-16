"use strict";

/* The frame around the screens: theme, account placeholders, the Archidekt
 * import dialog, and telling an open page that a newer version is live. */

/* ----------------------------------------------------------------- toast */

let toastTimer = null;
function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

/* ----------------------------------------------------------------- theme */

/* Deck mode paints the page in the deck's colour identity. Each colour has a
 * glow, a deep base and an accent light enough to read on the dark panels;
 * the gradient is built from whichever colours the commander has. */
const IDENTITY = {
  W: { glow: "#d8c486", deep: "#4a3f22", accent: "#f1dc9a" },
  U: { glow: "#2378c0", deep: "#0a2a4a", accent: "#7cc0f4" },
  B: { glow: "#5b4e55", deep: "#120f11", accent: "#cdb9c2" },
  R: { glow: "#c4412e", deep: "#43120b", accent: "#ff9c83" },
  G: { glow: "#2f8f58", deep: "#0c321d", accent: "#88dca6" },
  C: { glow: "#8c8e97", deep: "#25262b", accent: "#d9dae0" },
};

let deckIdentity = "";

function currentTheme() {
  try { return localStorage.getItem("mtgfish.theme") || "dark"; } catch (err) { return "dark"; }
}

function deckGradient(letters) {
  const colors = (letters || "").split("").filter((l) => IDENTITY[l]);
  if (!colors.length) colors.push("C");
  const glows = colors.map((l, i) => {
    const x = colors.length === 1 ? 20 : Math.round((i / (colors.length - 1)) * 90 + 5);
    return `radial-gradient(1100px 650px at ${x}% -8%, ${IDENTITY[l].glow}b3, transparent 62%)`;
  });
  const stops = colors.length === 1
    ? `${IDENTITY[colors[0]].deep} 0%, #0c0c0f 78%`
    : colors.map((l, i) => `${IDENTITY[l].deep} ${Math.round((i / (colors.length - 1)) * 100)}%`).join(", ");
  return `${glows.join(", ")}, linear-gradient(125deg, ${stops})`;
}

function applyTheme(theme) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  if (theme === "deck") {
    const colors = (deckIdentity || "C").split("");
    root.style.setProperty("--bg-grad", deckGradient(deckIdentity));
    root.style.setProperty("--accent", (IDENTITY[colors[0]] || IDENTITY.C).accent);
  } else {
    root.style.removeProperty("--bg-grad");
    root.style.removeProperty("--accent");
  }
  document.querySelectorAll("#themeswitch button").forEach((button) => {
    const on = button.dataset.themeChoice === theme;
    button.classList.toggle("active", on);
    button.setAttribute("aria-checked", String(on));
  });
  const results = document.getElementById("results");
  if (typeof report !== "undefined" && report && results.classList.contains("active")) drawCharts();
}

document.querySelectorAll("#themeswitch button").forEach((button) => {
  button.addEventListener("click", () => {
    const theme = button.dataset.themeChoice;
    try { localStorage.setItem("mtgfish.theme", theme); } catch (err) { /* ignore */ }
    applyTheme(theme);
    if (theme === "deck" && !deckIdentity) {
      if (typeof deckText === "function" && deckText()) loadDeckView(false);
      else toast("Deck mode takes its colours from your deck - load one to see them");
    }
  });
});

document.addEventListener("deckidentity", (event) => {
  deckIdentity = event.detail || "";
  try { localStorage.setItem("mtgfish.identity", deckIdentity); } catch (err) { /* ignore */ }
  if (currentTheme() === "deck") applyTheme("deck");
});

try { deckIdentity = localStorage.getItem("mtgfish.identity") || ""; } catch (err) { /* ignore */ }
applyTheme(currentTheme());

/* ---------------------------------------------------------- connectivity */

Backend.on("connection", (up) => {
  const dot = document.getElementById("connection");
  dot.classList.toggle("off", !up);
  dot.title = up ? "connected" : "reconnecting to the server...";
});

Backend.on("update-available", () => {
  document.getElementById("updatebanner").hidden = false;
});
document.getElementById("updatereload").addEventListener("click", () => location.reload());
document.getElementById("updatedismiss").addEventListener("click", () => {
  document.getElementById("updatebanner").hidden = true;
});

/* ------------------------------------------------------------ navigation */

document.querySelectorAll("[data-goto]").forEach((link) => {
  link.addEventListener("click", (event) => { event.preventDefault(); showTab(link.dataset.goto); });
});
document.getElementById("opendeck").addEventListener("click", () => showTab("deck"));

/* ------------------------------------------------------ account placeholders */

const authDialog = document.getElementById("dlg-auth");
function setAuthMode(mode) {
  document.querySelectorAll("[data-auth]").forEach((b) => b.classList.toggle("active", b.dataset.auth === mode));
  document.querySelectorAll("[data-auth-only]").forEach((node) => { node.hidden = node.dataset.authOnly !== mode; });
  document.getElementById("authtitle").textContent = mode === "login" ? "Welcome back" : "Create your account";
}
document.querySelectorAll("[data-auth]").forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.auth));
});
document.getElementById("btn-login").addEventListener("click", () => {
  setAuthMode("login");
  authDialog.showModal();
});
document.getElementById("btn-subscription").addEventListener("click", () => {
  document.getElementById("dlg-sub").showModal();
});

/* -------------------------------------------------------- archidekt import */

const arkDialog = document.getElementById("dlg-archidekt");
const arkMessage = document.getElementById("ark-message");
let arkPage = 1;
let arkOwner = "";

function arkSay(text, bad) {
  arkMessage.textContent = text || "";
  arkMessage.className = bad ? "hint bad" : "hint";
}

async function openImport() {
  document.querySelectorAll("#dlg-archidekt [data-requires]").forEach((node) => {
    node.hidden = !Backend.has(node.dataset.requires);
  });
  arkSay("");
  arkDialog.showModal();
  if (Backend.has("archidekt_account")) showAccount(await call("archidekt_account"));
}

function showAccount(account) {
  const signedIn = Boolean(account && account.signed_in);
  document.getElementById("ark-signedin").hidden = !signedIn;
  document.getElementById("ark-loginform").hidden = signedIn;
  document.getElementById("ark-user").textContent = signedIn ? account.username : "";
  const owner = document.getElementById("ark-owner");
  if (signedIn && !owner.value) owner.value = account.username;
}

document.querySelectorAll("#setupimport, #deckimport, [data-open-import]").forEach((button) => {
  button.addEventListener("click", openImport);
});
document.querySelectorAll("[data-close-dialog]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

function importInto(slot, payload) {
  const box = document.getElementById(`deck${slot}`);
  box.value = payload.decklist;
  saveSlot(slot, box.value);
  validateDeck(slot);
  arkDialog.close();
  const missing = payload.unresolved && payload.unresolved.length
    ? ` - ${payload.unresolved.length} card(s) not found` : "";
  toast(`Imported "${payload.name}" (${payload.cards} cards)${missing}`);
  showTab(slot === 0 ? "deck" : "setup");
}

document.getElementById("ark-linkform").addEventListener("submit", async (event) => {
  event.preventDefault();
  const link = document.getElementById("ark-link").value.trim();
  if (!link) return;
  arkSay("fetching the deck from Archidekt...");
  const reply = await call("archidekt_import", link);
  if (reply.missing) {
    // An older server: the paste box has always understood Archidekt links.
    importInto(Number(document.getElementById("ark-target").value), {
      name: "Archidekt deck", decklist: link, cards: "?", unresolved: [],
    });
    return;
  }
  if (reply.error) { arkSay(reply.error, true); return; }
  importInto(Number(document.getElementById("ark-target").value), reply);
});

document.getElementById("ark-loginform").addEventListener("submit", async (event) => {
  event.preventDefault();
  const user = document.getElementById("ark-username");
  const password = document.getElementById("ark-password");
  arkSay("signing in to Archidekt...");
  const reply = await call("archidekt_login", user.value, password.value);
  password.value = "";
  if (reply.error) { arkSay(reply.error, true); return; }
  arkSay("");
  showAccount(reply);
  document.getElementById("ark-owner").value = reply.username;
  loadDecks(reply.username, 1);
});

document.getElementById("ark-logout").addEventListener("click", async () => {
  showAccount(await call("archidekt_logout"));
  document.getElementById("ark-decks").textContent = "";
});

document.getElementById("ark-browseform").addEventListener("submit", (event) => {
  event.preventDefault();
  loadDecks(document.getElementById("ark-owner").value.trim(), 1);
});

async function loadDecks(owner, page) {
  const host = document.getElementById("ark-decks");
  if (page === 1) host.textContent = "";
  host.querySelector(".more")?.remove();
  arkSay("loading decks...");
  const reply = await call("archidekt_decks", owner, page);
  if (reply.error) { arkSay(reply.error, true); return; }
  arkOwner = reply.username;
  arkPage = page;
  arkSay(reply.count ? `${reply.count} deck${reply.count === 1 ? "" : "s"} by ${reply.username}` : `${reply.username} has no decks you can see`);

  reply.decks.forEach((deck) => {
    const row = el("div", "deckpick");
    const info = el("div");
    info.appendChild(el("div", null, deck.name));
    const meta = el("div", "meta");
    if (deck.colors) meta.appendChild(identitySymbols(deck.colors));
    if (deck.format) meta.appendChild(el("span", null, deck.format));
    if (deck.size) meta.appendChild(el("span", null, `${deck.size} cards`));
    if (deck.updated) meta.appendChild(el("span", null, `updated ${new Date(deck.updated).toLocaleDateString()}`));
    if (deck.private) meta.appendChild(el("span", "tag", "private"));
    if (deck.unlisted) meta.appendChild(el("span", "tag", "unlisted"));
    info.appendChild(meta);
    row.appendChild(info);

    const go = el("button", "primary small", "Import");
    go.addEventListener("click", async () => {
      go.disabled = true;
      arkSay(`importing ${deck.name}...`);
      const payload = await call("archidekt_import", String(deck.id));
      go.disabled = false;
      if (payload.error) { arkSay(payload.error, true); return; }
      importInto(Number(document.getElementById("ark-target").value), payload);
    });
    row.appendChild(go);
    host.appendChild(row);
  });

  if (reply.next) {
    const more = el("button", "ghost more", "Load more");
    more.addEventListener("click", () => loadDecks(arkOwner, arkPage + 1));
    host.appendChild(more);
  }
}
