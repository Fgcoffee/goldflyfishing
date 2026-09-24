"use strict";

/* The replay as a board, rather than as a document.
 *
 * A log says what happened; it does not say what the table looked like. After
 * fifty turns that difference is the whole thing - anyone who plays Magic
 * reads a board in a second and a log in ten minutes, and "your deck made five
 * thousand Ape tokens" is a sentence nobody should have to read to learn that
 * their deck made five thousand Ape tokens.
 *
 * Three things this view is for, in order:
 *
 *   what is on the battlefield, whose it is, and what is tapped;
 *   what is attacking, and whom;
 *   which permanents the parser could not read - visible as a blank the moment
 *   it lands, rather than inferred from a log that never mentions it again.
 *
 * The boards live on the Python side. The page is told where each one sits in
 * the log and fetches them one at a time, because the whole film is tens of
 * megabytes for one game and only ever one of them is on screen. */

const Board = (() => {
  let cards = [];        // the card dictionary: art, type line, parse status
  let frames = [];       // log frame index of each board, ascending
  let current = -1;      // which board is loaded
  let onJump = null;     // told when the viewer moves, so the log can follow

  /* Cards are stacked by name. A board with 5,000 Ape tokens drawn one card
   * per token is not a board, it is a denial of service - and the thing worth
   * seeing is the number anyway. */
  const STACK_FROM = 2;

  function reset(payload, jumpHandler) {
    cards = payload.board_cards || [];
    frames = payload.board_frames || [];
    current = -1;
    onJump = jumpHandler;
    const host = document.getElementById("replayboardview");
    host.innerHTML = "";
    const slider = document.getElementById("boardscrub");
    slider.max = Math.max(0, frames.length - 1);
    slider.value = 0;
    slider.disabled = frames.length === 0;
    document.getElementById("boardpanel").hidden = frames.length === 0;
    if (frames.length) show(0);
  }

  /* Which board is current at a log position: the last one at or before it.
   * Binary search because a long game has a couple of thousand of them and
   * this runs on every scroll of the log. */
  function indexForFrame(frame) {
    let low = 0;
    let high = frames.length - 1;
    let found = 0;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (frames[mid] <= frame) { found = mid; low = mid + 1; } else { high = mid - 1; }
    }
    return found;
  }

  async function showAtFrame(frame) {
    const index = indexForFrame(frame);
    if (index !== current) await show(index);
  }

  async function show(index) {
    if (!frames.length) return;
    index = Math.max(0, Math.min(index, frames.length - 1));
    const board = await call("replay_board", index);
    if (board.error) return;
    current = index;
    document.getElementById("boardscrub").value = index;
    draw(board);
  }

  function step(delta) {
    show(current + delta);
    if (onJump) onJump(frames[Math.max(0, Math.min(current + delta, frames.length - 1))]);
  }

  /* ------------------------------------------------------------- drawing */

  function draw(board) {
    document.getElementById("boardwhere").textContent =
      `turn ${board.turn} · ${board.phase.toLowerCase().replace(/_/g, " ")}`
      + ` / ${board.step.toLowerCase().replace(/_/g, " ")}`;
    drawInto(document.getElementById("replayboardview"), cards, board);
  }

  /* The whole board, into whatever element asked for it.
   *
   * The sandbox draws with this too. A bench that looked different from a
   * replay would be a second thing to learn for no reason, and the two do
   * differ in exactly one way: on a bench there is one operator who put every
   * card there themselves, so hands and graveyards are shown rather than
   * counted. Everything else is the same board. */
  function drawInto(host, table, board, options) {
    options = options || {};
    const was = cards;
    cards = table || [];
    try {
      host.innerHTML = "";
      const names = options.names || {};
      board.seats.forEach((seat) => {
        if (names[seat.player] === undefined) names[seat.player] = `P${seat.player}`;
      });

      const grid = el("div", "boardtable");
      board.seats.forEach((seat) => grid.appendChild(drawSeat(seat, board, names, options)));
      host.appendChild(grid);

      if (board.stack.length) host.appendChild(drawStack(board));
    } finally {
      // Only the replay keeps a card table between calls; a one-off draw must
      // not leave the scrubber pointing at somebody else's cards.
      if (options.keep !== true) cards = was;
    }
  }

  function drawSeat(seat, board, names, options) {
    const node = el("div", `boardseat seat-${seat.player}`
      + (seat.player === board.active ? " active" : "")
      + (seat.out ? " out" : ""));

    const head = el("div", "seathead");
    head.appendChild(el("span", "seatwho", names[seat.player]));
    head.appendChild(el("span", "seatlife", `${seat.life}`));
    const counts = el("span", "seatcounts");
    counts.appendChild(el("span", "count", `${seat.hand} hand`));
    counts.appendChild(el("span", "count", `${seat.library} library`));
    counts.appendChild(el("span", "count", `${seat.graveyard} yard`));
    if (seat.exile) counts.appendChild(el("span", "count", `${seat.exile} exile`));
    if (seat.poison) counts.appendChild(el("span", "count bad", `${seat.poison} poison`));
    head.appendChild(counts);
    if (seat.player === board.active) head.appendChild(el("span", "seatturn", "their turn"));
    if (seat.out) head.appendChild(el("span", "seatturn bad", "out"));
    node.appendChild(head);

    if (!seat.permanents.length) {
      node.appendChild(el("div", "boardempty", "nothing on the battlefield"));
      openZones(node, seat, board, names);
      return node;
    }

    /* Lands read as a block and everything else as individuals, which is how
     * a player looks at a board: you count the mana, then you read the cards. */
    const lands = [];
    const rest = [];
    seat.permanents.forEach((p) => {
      (isLand(p) ? lands : rest).push(p);
    });

    if (rest.length) node.appendChild(drawRow(rest, board, names));
    if (lands.length) node.appendChild(drawRow(lands, board, names, "lands"));
    openZones(node, seat, board, names);
    return node;
  }

  /* Hand and graveyard, when the caller is allowed to see them. A replay never
   * sends these - it would be showing information nobody at the table had. */
  function openZones(node, seat, board, names) {
    [["hand", seat.hand_cards], ["graveyard", seat.graveyard_cards]].forEach(
      ([label, contents]) => {
        if (!contents || !contents.length) return;
        node.appendChild(el("div", "zonename", label));
        const row = drawRow(contents, board, names, "openzone");
        node.appendChild(row);
      }
    );
  }

  function isLand(permanent) {
    const card = cards[permanent[1]];
    return card && /\bLand\b/.test(card.type_line);
  }

  /* One row of permanents, identical ones stacked into a single card with a
   * count on it. */
  function drawRow(permanents, board, names, extra) {
    const row = el("div", "boardrow" + (extra ? " " + extra : ""));
    const groups = new Map();
    permanents.forEach((p) => {
      // Anything with its own state - tapped, damaged, countered, attacking -
      // is worth seeing on its own; only plain duplicates collapse.
      const key = plain(p) ? `${p[1]}|${p[2]}` : `solo-${p[0]}`;
      const group = groups.get(key);
      if (group) group.push(p); else groups.set(key, [p]);
    });
    groups.forEach((group) => row.appendChild(drawCard(group, board, names)));
    return row;
  }

  function plain(permanent) {
    return permanent.length < 7;   // no counters, not attacking, not blocking
  }

  /* Art comes from Scryfall, over the network, and so is the one part of this
   * view that can simply not arrive: a token has no printing to fetch, the
   * desktop app may be offline, and Scryfall may be having a day. A card whose
   * art is missing still has to read as that card, so the placeholder keeps
   * the name and the frame and only loses the picture - a broken-image icon in
   * a row of eighty is worse than no picture at all. */
  function artFor(card) {
    if (!card.art) return el("div", "bart token");
    const img = el("img", "bart");
    img.loading = "lazy";
    img.src = card.art;
    img.alt = "";
    img.addEventListener("error", () => {
      const blank = el("div", "bart token");
      if (img.parentNode) img.parentNode.replaceChild(blank, img);
    });
    return img;
  }

  function drawCard(group, board, names) {
    const permanent = group[0];
    const card = cards[permanent[1]] || { name: "?", type_line: "", status: "ok" };
    const flags = permanent[2];
    const extra = permanent[6] || {};

    const node = el("div", "bcard"
      + ((flags & 1) ? " tapped" : "")
      + ((flags & 2) ? " sick" : "")
      + ((flags & 32) ? " phased" : "")
      + (card.status === "blank" || (flags & 64) ? " unread" : "")
      + (card.status === "partial" ? " partial" : "")
      + (extra.a !== undefined ? " attacking" : "")
      + (extra.b ? " blocking" : ""));

    node.appendChild(artFor(card));
    node.appendChild(el("div", "bname", card.name));

    const marks = el("div", "bmarks");
    if (group.length > STACK_FROM - 1 && group.length > 1) {
      marks.appendChild(el("span", "bcount", `x${group.length}`));
    }
    if (permanent[3] !== null && permanent[3] !== undefined) {
      const pt = el("span", "bpt", `${permanent[3]}/${permanent[4]}`);
      if (permanent[5]) pt.classList.add("hurt");
      marks.appendChild(pt);
    }
    if (permanent[5]) marks.appendChild(el("span", "bdmg", `${permanent[5]} dmg`));
    Object.entries(extra.c || {}).forEach(([kind, n]) => {
      marks.appendChild(el("span", "bcounter", `${n}×${kind}`));
    });
    if (flags & 8) marks.appendChild(el("span", "bcmd", "CMD"));
    if (marks.childNodes.length) node.appendChild(marks);

    if (extra.a !== undefined) {
      const at = extra.ap !== undefined && extra.ap >= 0
        ? "a permanent" : (names[extra.a] || `P${extra.a}`);
      node.appendChild(el("div", "battack", `attacking ${at}`));
    }
    if (extra.b) node.appendChild(el("div", "battack block", "blocking"));

    node.title = describe(card, permanent, group.length);
    node.addEventListener("click", () => zoom(card));
    return node;
  }

  function describe(card, permanent, count) {
    const lines = [card.name + (count > 1 ? ` (x${count})` : ""), card.type_line];
    if (permanent[2] & 1) lines.push("tapped");
    if (permanent[2] & 2) lines.push("summoning sick");
    if (card.status === "blank") {
      lines.push("THE PARSER READ NOTHING OF THIS CARD - it does nothing at all");
    } else if (card.status === "partial") {
      lines.push("partly read: " + (card.unread || []).join(" / "));
    }
    return lines.join("\n");
  }

  function drawStack(board) {
    const node = el("div", "boardstack");
    node.appendChild(el("div", "zonename", "Stack - top resolves first"));
    const row = el("div", "boardrow");
    board.stack.slice().reverse().forEach((item) => {
      const card = cards[item.card] || { name: "?" };
      const entry = el("div", `bcard onstack seat-${item.controller}`);
      entry.appendChild(artFor(card));
      entry.appendChild(el("div", "bname", card.name));
      if (item.text) entry.title = item.text;
      entry.addEventListener("click", () => zoom(card));
      row.appendChild(entry);
    });
    node.appendChild(row);
    return node;
  }

  /* The full card, because the board is drawn from art crops and sometimes the
   * question is "what does this actually say". */
  function zoom(card) {
    const host = document.getElementById("boardzoom");
    host.innerHTML = "";
    host.hidden = false;
    if (card.image) {
      const img = el("img");
      img.src = card.image;
      img.alt = card.name;
      img.addEventListener("error", () => img.remove());
      host.appendChild(img);
    }
    const info = el("div", "zoominfo");
    info.appendChild(el("h3", null, card.name));
    info.appendChild(el("div", "dim", card.type_line));
    if (card.status === "blank") {
      info.appendChild(el("div", "reading fail",
        "The parser read nothing of this card. On the battlefield it is a blank: "
        + "every number this run produced understates it."));
    } else if (card.status === "partial") {
      info.appendChild(el("div", "reading fail",
        "Partly read. These abilities never fired: " + (card.unread || []).join("; ")));
    } else if (card.token) {
      info.appendChild(el("div", "dim", "A token - no printing, so no art."));
    } else {
      info.appendChild(el("div", "reading ok", "The parser read this card completely."));
    }
    const close = el("button", "ghost small", "Close");
    close.addEventListener("click", () => { host.hidden = true; });
    info.appendChild(close);
    host.appendChild(info);
  }

  return { reset, show, showAtFrame, step, indexForFrame, drawInto,
           get count() { return frames.length; },
           get frames() { return frames; },
           get current() { return current; } };
})();
