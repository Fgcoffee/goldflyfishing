"use strict";

/* The Deck tab: every card in deck 1, as images, marked by how well the
 * parser read it.
 *
 * The decklist text in the first slot of the Decks screen stays the one source
 * of truth - it is what a simulation runs. Editing here rewrites that text and
 * asks the bridge to read it again, so the two screens cannot disagree about
 * what the deck is.
 */

const Deck = {
  view: null,        // the last deck_view reply
  text: null,        // the text that reply describes
  filter: "all",
  group: "type",
  mode: "grid",
  drawerRow: null,
  showingBack: false,
};

const STATUS_TEXT = {
  ok: "Fully read",
  partial: "Partly read",
  blank: "Unread",
  off: "Switched off",
  missing: "Not found",
};
const ATTENTION = new Set(["partial", "blank", "off", "missing"]);
const TYPE_ORDER = ["Commander", "Creature", "Planeswalker", "Battle", "Instant", "Sorcery",
  "Artifact", "Enchantment", "Land", "Other", "Not found"];
const MANA_COLORS = { W: "#e6d9a8", U: "#3f8fd2", B: "#8a7c80", R: "#dc5f47", G: "#4a9d68", C: "#a7a8b0" };

/* ------------------------------------------------------------- symbols */

function manaSymbol(symbol, large) {
  const img = document.createElement("img");
  img.className = large ? "ms lg" : "ms";
  img.alt = `{${symbol}}`;
  img.title = `{${symbol}}`;
  img.src = `https://svgs.scryfall.io/card-symbols/${symbol.replace(/\//g, "").toUpperCase()}.svg`;
  return img;
}

function manaCost(text) {
  const box = el("span", "cost");
  (text || "").split(" // ").forEach((half, index) => {
    if (index) box.appendChild(document.createTextNode(" // "));
    for (const match of half.matchAll(/\{([^}]+)\}/g)) box.appendChild(manaSymbol(match[1]));
  });
  return box;
}

function identitySymbols(letters) {
  const box = el("span", "cost");
  (letters || "C").split("").forEach((letter) => box.appendChild(manaSymbol(letter, true)));
  return box;
}

/* --------------------------------------------------------------- loading */

function deckText() {
  const box = document.getElementById("deck0");
  return box ? box.value.trim() : "";
}

async function loadDeckView(force) {
  const text = deckText();
  if (!text) {
    Deck.view = null;
    Deck.text = null;
    renderDeck();
    return;
  }
  if (!force && Deck.view && text === Deck.text) { renderDeck(); return; }

  setStatus("reading the deck...");
  document.getElementById("deckgrid").classList.add("loading");
  const view = await call("deck_view", text);
  document.getElementById("deckgrid").classList.remove("loading");
  if (view.error) {
    if (!view.missing) toast(view.error);
    return;
  }
  Deck.view = view;
  Deck.text = text;
  document.dispatchEvent(new CustomEvent("deckidentity", { detail: view.color_identity }));
  renderDeck();
  setStatus(`${view.stats.total} cards read`);
}

/* All rows the grid can show, commanders and names that matched nothing
 * included, each carrying what the grouping needs. */
function deckRows() {
  const view = Deck.view;
  const rows = [];
  view.commanders.forEach((card) => rows.push(Object.assign({ commander: true }, card)));
  view.cards.forEach((card) => rows.push(card));
  const counts = {};
  view.unresolved.forEach((name) => { counts[name] = (counts[name] || 0) + 1; });
  Object.entries(counts).forEach(([name, quantity]) => rows.push({
    name, quantity, status: "missing", type_group: "Not found", mana_value: 0,
    images: null, mana_cost: "", type_line: "", missing: true, abilities: 0, understood: 0,
  }));
  return rows;
}

function groupOf(row) {
  if (row.missing) return "Not found";
  if (Deck.group === "status") return STATUS_TEXT[row.status];
  if (row.commander) return "Commander";
  if (Deck.group === "mv") return row.is_land ? "Land" : (row.mana_value >= 7 ? "7+" : String(row.mana_value));
  return row.type_group;
}

function groupOrder(name) {
  if (Deck.group === "status") return ["Unread", "Partly read", "Switched off", "Not found", "Fully read"].indexOf(name);
  if (Deck.group === "mv") {
    if (name === "Commander") return -1;
    if (name === "Land") return 20;
    if (name === "Not found") return 21;
    return name === "7+" ? 7 : Number(name);
  }
  const index = TYPE_ORDER.indexOf(name);
  return index < 0 ? 50 : index;
}

/* ------------------------------------------------------------- rendering */

function renderDeck() {
  const view = Deck.view;
  const empty = document.getElementById("deckempty");
  const parts = ["deckgrid", "deckissues", "deckstats"].map((id) => document.getElementById(id));
  empty.hidden = Boolean(view);
  parts.forEach((node) => { node.hidden = !view; });
  if (!view) {
    document.getElementById("deckname").textContent = "No deck loaded";
    document.getElementById("deckcommanders").textContent = "";
    document.getElementById("deckchips").textContent = "";
    document.querySelector("#deckhero .hero-art").style.backgroundImage = "";
    return;
  }
  renderHero(view);
  renderIssues(view);
  renderCards();
  renderStats(view.stats);
  if (Deck.drawerRow) {
    const fresh = deckRows().find((row) => row.name === Deck.drawerRow.name);
    if (fresh) fillDrawer(fresh, false); else closeDrawer();
  }
}

function renderHero(view) {
  const lead = view.commanders[0];
  document.querySelector("#deckhero .hero-art").style.backgroundImage =
    lead && lead.images ? `url("${lead.images.art}")` : "";

  const archidekt = (Deck.text || "").match(/archidekt\.com\/(?:api\/)?decks\/(\d+)/);
  const kicker = document.getElementById("deckkicker");
  kicker.textContent = archidekt ? "Linked from Archidekt" : "Your deck";
  document.getElementById("deckname").textContent =
    view.name && view.name !== "Your deck" ? view.name : (lead ? lead.name : "Your deck");

  const sub = document.getElementById("deckcommanders");
  sub.textContent = "";
  sub.appendChild(identitySymbols(view.color_identity));
  sub.appendChild(document.createTextNode(
    view.commanders.length ? view.commanders.map((c) => c.name).join(" + ") : "no commander"));

  const stats = view.stats;
  const chips = document.getElementById("deckchips");
  chips.textContent = "";
  const chip = (label, value, tone, filter) => {
    const node = el("span", `chip-stat${tone ? " " + tone : ""}`);
    node.appendChild(el("b", null, String(value)));
    node.appendChild(document.createTextNode(label));
    if (filter) {
      node.style.cursor = "pointer";
      node.title = "show only these";
      node.addEventListener("click", () => setFilter(filter));
    }
    chips.appendChild(node);
  };
  const pct = stats.abilities ? Math.round((stats.abilities_understood / stats.abilities) * 100) : 100;
  chip(" / 100 cards", stats.total, stats.total === 100 ? "" : "warn");
  chip(" lands", stats.lands);
  chip(" avg mana value", stats.average_mana_value.toFixed(2));
  chip("% of abilities understood", pct, pct >= 90 ? "good" : pct >= 75 ? "warn" : "bad");
  if (stats.status.partial) chip(" partly read", stats.status.partial, "warn", "partial");
  if (stats.status.blank) chip(" unread", stats.status.blank, "bad", "blank");
  if (view.unresolved.length) chip(" not found", view.unresolved.length, "bad", "attention");
}

function renderIssues(view) {
  const host = document.getElementById("deckissues");
  host.textContent = "";
  view.issues
    .filter((issue) => issue.severity !== "info" || /inferred|Did you mean/.test(issue.message))
    .forEach((issue) => host.appendChild(el("div", `issue ${issue.severity}`, issue.message)));
}

function renderCards() {
  const host = document.getElementById("deckgrid");
  host.textContent = "";
  const rows = deckRows().filter((row) => {
    if (Deck.filter === "all") return true;
    if (Deck.filter === "attention") return ATTENTION.has(row.status);
    return row.status === Deck.filter;
  });

  const counts = { attention: 0, partial: 0, blank: 0 };
  deckRows().forEach((row) => {
    if (ATTENTION.has(row.status)) counts.attention += row.quantity;
    if (row.status === "partial") counts.partial += row.quantity;
    if (row.status === "blank") counts.blank += row.quantity;
  });
  document.querySelectorAll("#deckfilter button").forEach((button) => {
    const key = button.dataset.filter;
    const base = { all: "All", attention: "Needs attention", partial: "Partial", blank: "Unread" }[key];
    const label = key === "all" ? base : `${base} (${counts[key]})`;
    const swatch = button.querySelector(".sw");
    button.textContent = "";
    if (swatch) button.appendChild(swatch);
    button.appendChild(document.createTextNode(label));
  });

  if (!rows.length) {
    host.appendChild(el("p", "hint", Deck.filter === "all"
      ? "This deck has no cards." : "Nothing here - every card in this deck is fully read."));
    return;
  }

  const groups = new Map();
  rows.forEach((row) => {
    const name = groupOf(row);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(row);
  });
  [...groups.entries()]
    .sort((a, b) => groupOrder(a[0]) - groupOrder(b[0]))
    .forEach(([name, members]) => {
      members.sort((a, b) => (a.mana_value - b.mana_value) || a.name.localeCompare(b.name));
      const section = el("div", "group");
      const head = el("div", "group-head");
      head.appendChild(el("h3", null, name));
      head.appendChild(el("span", "count", String(members.reduce((n, r) => n + r.quantity, 0))));
      section.appendChild(head);
      section.appendChild(Deck.mode === "list" ? cardTable(members) : cardGrid(members));
      host.appendChild(section);
    });
}

function cardGrid(rows) {
  const grid = el("div", "cardgrid");
  rows.forEach((row) => grid.appendChild(cardTile(row)));
  return grid;
}

function cardTile(row) {
  const tile = el("div", `tile ${row.status}${row.commander ? " commander" : ""}`);
  tile.tabIndex = 0;
  tile.title = row.name;

  const fallback = () => {
    const box = el("div", "fallback");
    box.appendChild(el("strong", null, row.name));
    if (row.mana_cost) box.appendChild(manaCost(row.mana_cost));
    if (row.type_line) box.appendChild(el("span", "dim", row.type_line));
    return box;
  };
  if (row.images) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = row.name;
    img.src = row.images.normal;
    img.addEventListener("error", () => { img.replaceWith(fallback()); });
    tile.appendChild(img);
  } else {
    tile.appendChild(fallback());
  }

  if (row.quantity > 1) tile.appendChild(el("span", "qty", `${row.quantity}x`));
  if (row.status !== "ok") {
    const text = row.status === "partial"
      ? `Partly read - ${row.understood}/${row.abilities}` : STATUS_TEXT[row.status];
    tile.appendChild(el("span", "badge", text));
  }

  const actions = el("div", "tile-actions");
  const action = (label, title, fn) => {
    const button = el("button", null, label);
    button.title = title;
    button.addEventListener("click", (event) => { event.stopPropagation(); fn(); });
    actions.appendChild(button);
  };
  if (!row.commander && !row.missing) {
    action("−", "one fewer", () => changeQuantity(row.name, -1));
    action("+", "one more", () => changeQuantity(row.name, +1));
  }
  action("×", row.commander ? "remove as commander" : "remove from deck", () => removeCard(row));
  tile.appendChild(actions);

  tile.addEventListener("click", () => openDrawer(row));
  tile.addEventListener("keydown", (event) => { if (event.key === "Enter") openDrawer(row); });
  return tile;
}

function cardTable(rows) {
  const table = el("table", "cardlist");
  const body = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr", row.status);
    tr.appendChild(el("td", "q", String(row.quantity)));
    const name = el("td");
    name.appendChild(el("span", null, row.name));
    tr.appendChild(name);
    tr.appendChild(el("td", "t", row.type_line || ""));
    const cost = el("td", "c");
    cost.appendChild(manaCost(row.mana_cost));
    tr.appendChild(cost);
    const status = el("td", "s");
    status.appendChild(el("span", `pill ${row.status}`,
      row.status === "partial" ? `Partly read ${row.understood}/${row.abilities}` : STATUS_TEXT[row.status]));
    tr.appendChild(status);
    tr.addEventListener("click", () => openDrawer(row));
    body.appendChild(tr);
  });
  table.appendChild(body);
  return table;
}

/* ----------------------------------------------------------------- stats */

function renderStats(stats) {
  // Curve
  const curve = document.getElementById("curve");
  curve.textContent = "";
  const buckets = ["0", "1", "2", "3", "4", "5", "6", "7"];
  const peak = Math.max(1, ...buckets.map((b) => stats.curve[b] || 0));
  buckets.forEach((bucket) => {
    const count = stats.curve[bucket] || 0;
    const bar = el("div", "bar");
    bar.appendChild(el("span", "n", count ? String(count) : ""));
    const fill = el("div", "fill");
    fill.style.height = `${Math.round((count / peak) * 82)}%`;
    const breakdown = Object.entries(stats.curve_by_type[bucket] || {})
      .sort((a, b) => b[1] - a[1]).map(([t, n]) => `${n} ${t}`).join(", ");
    bar.title = `${count} at mana value ${bucket === "7" ? "7+" : bucket}${breakdown ? ": " + breakdown : ""}`;
    bar.appendChild(fill);
    bar.appendChild(el("span", "lbl", bucket === "7" ? "7+" : bucket));
    curve.appendChild(bar);
  });
  document.getElementById("avgmv").textContent =
    `${stats.spells} spells - average ${stats.average_mana_value.toFixed(2)}`;

  // Pips against land sources
  const pips = document.getElementById("pips");
  pips.textContent = "";
  const totalPips = Object.values(stats.pips).reduce((a, b) => a + b, 0) || 1;
  const letters = ["W", "U", "B", "R", "G", "C"].filter((l) => stats.pips[l] || stats.sources[l]);
  if (!letters.length) pips.appendChild(el("span", "dim", "no coloured costs"));
  letters.forEach((letter) => {
    const row = el("div", "pip-row");
    row.appendChild(manaSymbol(letter, true));
    const bars = el("div", "pip-bars");
    const bar = (share, cls, title) => {
      const track = el("div", `pip-bar ${cls}`);
      const fill = document.createElement("i");
      fill.style.width = `${Math.round(share * 100)}%`;
      fill.style.background = MANA_COLORS[letter];
      track.title = title;
      track.appendChild(fill);
      bars.appendChild(track);
    };
    const pipCount = stats.pips[letter] || 0;
    const sourceCount = stats.sources[letter] || 0;
    bar(pipCount / totalPips, "cost", `${pipCount} ${letter} symbols in costs (${Math.round((pipCount / totalPips) * 100)}%)`);
    bar(stats.lands ? sourceCount / stats.lands : 0, "src", `${sourceCount} of ${stats.lands} lands make ${letter}`);
    row.appendChild(bars);
    row.appendChild(el("span", "pip-num", `${pipCount} pips / ${sourceCount} src`));
    pips.appendChild(row);
  });

  // Types
  const types = document.getElementById("types");
  types.textContent = "";
  Object.entries(stats.types)
    .sort((a, b) => TYPE_ORDER.indexOf(a[0]) - TYPE_ORDER.indexOf(b[0]))
    .forEach(([name, count]) => {
      types.appendChild(el("span", null, name));
      types.appendChild(el("span", null, String(count)));
    });

  // Parse quality
  const quality = document.getElementById("quality");
  quality.textContent = "";
  const pct = stats.abilities ? Math.round((stats.abilities_understood / stats.abilities) * 100) : 100;
  const big = el("div");
  big.appendChild(el("span", "big", `${pct}%`));
  big.appendChild(el("span", "dim", ` of ${stats.abilities} abilities understood`));
  quality.appendChild(big);

  const missing = Deck.view.unresolved.length;
  const segments = [
    ["ok", "Fully read", "var(--good)", stats.status.ok || 0],
    ["partial", "Partly read", "var(--warn)", stats.status.partial || 0],
    ["blank", "Unread", "var(--bad)", stats.status.blank || 0],
    ["off", "Switched off", "var(--faint)", stats.status.off || 0],
    ["attention", "Not found", "var(--bad)", missing],
  ];
  const total = segments.reduce((n, s) => n + s[3], 0) || 1;
  const stack = el("div", "stackbar");
  segments.forEach(([, label, color, count]) => {
    if (!count) return;
    const part = document.createElement("i");
    part.style.width = `${(count / total) * 100}%`;
    part.style.background = color;
    part.title = `${count} ${label.toLowerCase()}`;
    stack.appendChild(part);
  });
  quality.appendChild(stack);

  const legend = el("div", "legend");
  segments.forEach(([key, label, color, count]) => {
    if (!count && key !== "ok") return;
    const swatch = el("i", "sw");
    swatch.style.background = color;
    legend.appendChild(swatch);
    const name = el("button", null, label);
    name.addEventListener("click", () => setFilter(key === "ok" ? "all" : key));
    legend.appendChild(name);
    legend.appendChild(el("span", "dim", String(count)));
  });
  quality.appendChild(legend);
}

/* --------------------------------------------------------------- editing */

function deckModel() {
  const view = Deck.view;
  return {
    commanders: view.commanders.map((c) => ({ name: c.name, quantity: c.quantity })),
    cards: view.cards.map((c) => ({ name: c.name, quantity: c.quantity })),
    unresolved: [...view.unresolved],
  };
}

function serializeDeck(model) {
  const lines = model.cards.filter((c) => c.quantity > 0).map((c) => `${c.quantity} ${c.name}`);
  model.unresolved.forEach((name) => lines.push(`1 ${name}`));
  if (model.commanders.length) {
    lines.push("", "// Commander");
    model.commanders.forEach((c) => lines.push(`${c.quantity} ${c.name}`));
  }
  return lines.join("\n");
}

async function editDeck(mutate, message) {
  if (!Deck.view) return;
  const wasLink = /archidekt\.com\/(?:api\/)?decks\//.test(Deck.text || "");
  const model = deckModel();
  mutate(model);
  const box = document.getElementById("deck0");
  box.value = serializeDeck(model);
  saveSlot(0, box.value);
  validateDeck(0);
  await loadDeckView(true);
  toast(wasLink ? `${message} - the Archidekt link is now an editable list` : message);
}

function changeQuantity(name, delta) {
  editDeck((model) => {
    const card = model.cards.find((c) => c.name === name);
    if (!card) return;
    card.quantity += delta;
    if (card.quantity <= 0) model.cards.splice(model.cards.indexOf(card), 1);
  }, delta > 0 ? `Added a ${name}` : `Removed a ${name}`);
}

function removeCard(row) {
  editDeck((model) => {
    if (row.missing) {
      model.unresolved = model.unresolved.filter((n) => n !== row.name);
    } else if (row.commander) {
      model.commanders = model.commanders.filter((c) => c.name !== row.name);
    } else {
      model.cards = model.cards.filter((c) => c.name !== row.name);
    }
  }, `Removed ${row.name}`);
}

function addCard(name) {
  editDeck((model) => {
    const card = model.cards.find((c) => c.name === name);
    if (card) card.quantity += 1;
    else model.cards.push({ name, quantity: 1 });
  }, `Added ${name}`);
}

function makeCommander(name) {
  editDeck((model) => {
    const card = model.cards.find((c) => c.name === name);
    if (card) {
      card.quantity -= 1;
      if (card.quantity <= 0) model.cards.splice(model.cards.indexOf(card), 1);
    }
    if (!model.commanders.find((c) => c.name === name)) model.commanders.push({ name, quantity: 1 });
  }, `${name} is now a commander`);
}

function demoteCommander(name) {
  editDeck((model) => {
    model.commanders = model.commanders.filter((c) => c.name !== name);
    model.cards.unshift({ name, quantity: 1 });
  }, `${name} moved into the deck`);
}

/* ---------------------------------------------------------- adding cards */

const addBox = document.getElementById("addcard");
const addList = document.getElementById("addresults");
let addTimer = null;
let addHot = -1;

addBox.addEventListener("input", () => {
  clearTimeout(addTimer);
  addTimer = setTimeout(runAddSearch, 150);
});
addBox.addEventListener("keydown", (event) => {
  const items = [...addList.querySelectorAll("li")];
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!items.length) return;
    addHot = (addHot + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((item, i) => item.classList.toggle("hot", i === addHot));
    items[addHot].scrollIntoView({ block: "nearest" });
  } else if (event.key === "Enter") {
    event.preventDefault();
    const pick = items[addHot >= 0 ? addHot : 0];
    if (pick) pick.click();
  } else if (event.key === "Escape") {
    addList.hidden = true;
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".addcard")) addList.hidden = true;
});

async function runAddSearch() {
  const text = addBox.value.trim();
  addHot = -1;
  if (text.length < 2) { addList.hidden = true; return; }
  const reply = await call("card_search", text);
  if (reply.error || text !== addBox.value.trim()) return;
  addList.textContent = "";
  const inDeck = {};
  if (Deck.view) deckRows().forEach((row) => { inDeck[row.name] = row.quantity; });

  (reply.cards || []).forEach((card) => {
    const item = el("li");
    const art = document.createElement("img");
    art.loading = "lazy";
    art.alt = "";
    art.src = card.images.art;
    item.appendChild(art);
    const text = el("div");
    const title = el("div", "nm", card.name);
    text.appendChild(title);
    text.appendChild(el("div", "tl", card.type_line));
    item.appendChild(text);
    const side = el("div");
    side.style.textAlign = "right";
    side.appendChild(manaCost(card.mana_cost));
    const note = inDeck[card.name]
      ? el("div", "qual dim", `in deck x${inDeck[card.name]}`)
      : el("div", `qual ${card.status === "ok" ? "good" : card.status === "partial" ? "warn" : "bad"}`,
        STATUS_TEXT[card.status]);
    side.appendChild(note);
    item.appendChild(side);
    item.addEventListener("click", () => {
      addList.hidden = true;
      addBox.value = "";
      if (!Deck.view) { toast("Load a deck first"); return; }
      addCard(card.name);
    });
    addList.appendChild(item);
  });
  if (!addList.children.length) addList.appendChild(el("li", "dim", "no card by that name"));
  addList.hidden = false;
}

/* ---------------------------------------------------------------- drawer */

function openDrawer(row) {
  Deck.showingBack = false;
  fillDrawer(row, true);
  document.getElementById("carddrawer").hidden = false;
  document.getElementById("scrim").hidden = false;
}

function closeDrawer() {
  Deck.drawerRow = null;
  document.getElementById("carddrawer").hidden = true;
  document.getElementById("scrim").hidden = true;
}

function fillDrawer(row, fetchReading) {
  Deck.drawerRow = row;
  const img = document.getElementById("drawerimg");
  const flip = document.getElementById("drawerflip");
  if (row.images) {
    img.hidden = false;
    img.src = Deck.showingBack && row.images.back ? row.images.back.replace("/normal/", "/large/") : row.images.large;
    img.alt = row.name;
  } else {
    img.hidden = true;
  }
  flip.hidden = !(row.images && row.images.back);
  flip.onclick = () => { Deck.showingBack = !Deck.showingBack; fillDrawer(row, false); };

  document.getElementById("drawername").textContent = row.name;
  const type = document.getElementById("drawertype");
  type.textContent = "";
  if (row.mana_cost) { type.appendChild(manaCost(row.mana_cost)); type.appendChild(document.createTextNode("  ")); }
  type.appendChild(document.createTextNode(row.type_line || ""));

  const status = document.getElementById("drawerstatus");
  status.textContent = "";
  const lines = {
    ok: row.abilities ? `The engine reads all ${row.abilities} of this card's abilities.`
      : "Nothing on this card needs reading.",
    partial: `The engine reads ${row.understood} of ${row.abilities} abilities. The rest do nothing in simulations.`,
    blank: "The engine could not read this card. In simulations it is a body and a mana cost, nothing more.",
    off: "Switched off by you in the sandbox - its abilities are withheld from every run.",
    missing: "No card by this name. Check the spelling, or remove it.",
  };
  status.appendChild(el("div", `verdictline ${row.status}`, lines[row.status]));

  const actions = document.getElementById("draweractions");
  actions.textContent = "";
  const button = (label, fn, cls) => {
    const node = el("button", cls || null, label);
    node.addEventListener("click", fn);
    actions.appendChild(node);
  };
  if (!row.missing && !row.commander) {
    button("−", () => changeQuantity(row.name, -1));
    actions.appendChild(el("span", null, `x${row.quantity}`));
    button("+", () => changeQuantity(row.name, +1));
    if (row.can_be_commander) button("Make commander", () => makeCommander(row.name), "ghost");
  }
  if (row.commander) button("Move into the deck", () => demoteCommander(row.name), "ghost");
  button("Remove", () => { removeCard(row); closeDrawer(); }, "ghost");
  if (!row.missing) {
    button("Test in sandbox", () => {
      closeDrawer();
      showTab("sandbox");
      document.getElementById("cardsearch").value = row.name;
      chooseCard(row.name, null);
    }, "ghost");
  }

  const reading = document.getElementById("drawerreading");
  if (!fetchReading) return;
  reading.textContent = "";
  if (row.missing) return;
  if (row.oracle_text) reading.appendChild(el("div", "oracle", row.oracle_text));
  loadReading(row.name, reading);
}

async function loadReading(name, host) {
  const info = await call("sandbox_inspect", name);
  if (info.error || !Deck.drawerRow || Deck.drawerRow.name !== name) return;
  host.appendChild(el("h3", null, "What the engine makes of it"));
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
      host.appendChild(block);
    });
    face.failures.forEach((failure) => {
      host.appendChild(el("div", "reading fail",
        `unread: ${failure.reason} at "${failure.stopped_at}" - ${failure.remaining}`));
    });
  });
}

document.querySelectorAll("[data-close-drawer]").forEach((node) => node.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.getElementById("carddrawer").hidden) closeDrawer();
});

/* -------------------------------------------------------------- controls */

function setFilter(filter) {
  Deck.filter = filter;
  document.querySelectorAll("#deckfilter button").forEach((b) => b.classList.toggle("active", b.dataset.filter === filter));
  if (Deck.view) renderCards();
}

document.querySelectorAll("#deckfilter button").forEach((button) => {
  button.addEventListener("click", () => setFilter(button.dataset.filter));
});
document.querySelectorAll("#deckmode button").forEach((button) => {
  button.addEventListener("click", () => {
    Deck.mode = button.dataset.mode;
    document.querySelectorAll("#deckmode button").forEach((b) => b.classList.toggle("active", b === button));
    try { localStorage.setItem("mtgfish.deckmode", Deck.mode); } catch (err) { /* ignore */ }
    if (Deck.view) renderCards();
  });
});
document.getElementById("deckgroup").addEventListener("change", (event) => {
  Deck.group = event.target.value;
  if (Deck.view) renderCards();
});

const dock = document.getElementById("deckstats");
document.getElementById("dockToggle").addEventListener("click", () => {
  const collapsed = dock.classList.toggle("collapsed");
  document.getElementById("dockToggle").setAttribute("aria-expanded", String(!collapsed));
  try { localStorage.setItem("mtgfish.dock", collapsed ? "collapsed" : "open"); } catch (err) { /* ignore */ }
});

try {
  if (localStorage.getItem("mtgfish.dock") === "collapsed") dock.classList.add("collapsed");
  const mode = localStorage.getItem("mtgfish.deckmode");
  if (mode) document.querySelector(`#deckmode button[data-mode="${mode}"]`)?.click();
} catch (err) { /* ignore */ }

document.addEventListener("tabchange", (event) => {
  if (event.detail === "deck") loadDeckView(false);
});
document.addEventListener("deckchange", () => {
  const active = document.getElementById("deck").classList.contains("active");
  if (active || document.documentElement.dataset.theme === "deck") loadDeckView(true);
  else Deck.text = null;
});
document.addEventListener("appready", () => {
  if (deckText()) loadDeckView(false);
  else renderDeck();
});
