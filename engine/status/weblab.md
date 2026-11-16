# weblab session: status

## Current job: card-reading correctness (from 16 Nov, user's go-ahead)

Catch cards the parser marks fully read but reads **wrong**. Owned paths:
`tests/ir/test_golden_readings.py`, `tests/ir/golden/`,
`engine/status/readings_queue.md`, and the site files for "dummy abilities"
later. Not `mtgfish/parser`.

- **Item 1, golden readings for the top 300 (by EDHREC rank): reviewed.**
  - 274 of 300 are marked fully read. Each ability's Oracle line was checked
    against `explain_card` (rendered from the opcodes), and against the IR
    where the English was ambiguous.
  - **16 fully-read cards are misread.** They're queued in
    `engine/status/readings_queue.md` for the parser agent, grouped by cause:
    - mana abilities that lose their colour limit (Exotic Orchard, Fellwar
      Stone, Chrome Mox, Reflecting Pool, Mox Amber, Bloom Tender);
    - the channel lands' "costs {1} less to activate" read as a resolution
      effect (Boseiju, Otawara, Takenuma);
    - one-offs: Mystic Sanctuary, The Great Henge, Dovin's Veto, Rishkar's
      Expertise, Panharmonicon, Ragavan (cast for free instead of paying),
      Dryad of the Ilysian Grove.
  - The other 258 go into `tests/ir/golden/readings.json`, pinned by
    `tests/ir/test_golden_readings.py`: one test per card; a failure shows the
    old and new reading, and the docstring says how to refresh a card.
  - Committed in e6d51c1; 259 tests pass. Phyrexian Arena was refreshed for
    the 15 Nov snapshot's rewording (same reading).
- **Item 2, batch spot checks: tool in place, first batch checked.**
  - `python -m tests.ir.readings_spot_check BASE_REV` parses the pool at
    BASE_REV (git worktree) and at HEAD against the same card database.
    It prints the fully-read counts and a sample of 30 newly fully-read
    cards (`--sample`, `--seed`), with Oracle beside reading. It takes a
    few minutes.
  - **Batch b2ce87b** ("is equipped / enchanted"): +17 fully read, all
    checked. 12 are misread, confirmed on the TS engine: "it" in the static
    ability is `remembered`, so the bonus goes to every permanent (an
    equipped Kor Duelist gives a Grizzly Bears double strike). Queued with
    the cause and a suggested guard.
  - Next: run it on every new `[ir]` batch (`git log --grep='^\[ir\]'`).
  - **Batch 0100e79** (dummy abilities, the "it" fix): +37, -2 (on
    purpose). Sample of 30 checked. The fix holds on all 13 queued cards,
    which are now in the golden set (271 cards). Two low-priority issues
    queued: activated dummies lose their cost, and Branch of Vitu-Ghazi has
    Disguise twice.
  - Last batch checked: 0100e79.
- **Item 3, dummy abilities on the site: done (752b7a4).**
  - `deckview.parse_status` gives `recognised`, the dummy rule names, as
    the TS `parseStatus` does; the bridge's board cards carry it too.
  - Deck tab: a "recognised, not implemented yet" chip, a "Not implemented"
    filter (also counted under "Needs attention"), and a badge on each tile.
    List view gets a pill; the card window gets a line naming each shape,
    in place of the green "reads all of it".
  - Replay card window and board tooltip: the same line.
  - Its own colour (`--todo`) in each theme.
  - Checked in headless Chromium against a local server.
- **Full suite after dd5cc93: 3708 passed, 3 failed**: the three `test_keywords` failures below. The parser fixed the explain one, and `test_cancel` passed this time. Earlier, after afb8f0b: 3681 passed, 5 failed. None of the
  failures come from this track, which only adds files under `tests/ir/`.
  - Four come from the 15 Nov Scryfall snapshot (38875b6). Its new keywords
    and ability words aren't registered yet: Affinitycycling, Beam me up,
    Halflingcycling, Empower Jace, Actualize, Battlefront, Federation, Seek
    out, Strange new worlds. Also one effect opcode without a phrasing in
    `test_explain` (11 of 10 allowed). For the rules and parser agents.
  - `tests/sim/test_cancel.py::test_a_parallel_run_stops_and_leaves_no_workers`
    took 206 s against a 180 s limit under the full `-n 4` load. It passes
    alone (4 passed). Flaky under load.
- **Later:** item 3, dummy abilities on the Deck tab and in the card window,
  once the parser publishes the IR field (see `engine/status/parser.md`).

# Earlier job: Cut lab and Recs restore (done)

Owner of this file: the web agent restoring PR #28's Cut lab and Recs tabs.
Owner paths for this job: `mtgfish/ui/web/`, `mtgfish/web/`, `mtgfish/ui/bridge.py`,
`mtgfish/sim/cutlab.py`, `mtgfish/sim/runner.py` (`_worker_state` only),
`mtgfish/data/recs.py`, and their tests.

## Done

- **Ported PR #28** (`worktree-cut-lab-recs`, d90ca2e) onto
  `claude/modest-bohr-yqrqm7`. It cherry-picked with only two conflicts, both
  additive (`index.html` nav and sections, `style.css`): the Cut lab and Recs
  tabs sit beside My decks / History / Swap lab. The web server already listed
  `start_cutlab` / `start_queue_test` in `GAME_ARGS` and `FINISH_SIGNALS`, and
  `cutlab_status` as a transient signal, so the slots come through the
  `/api/call` path, with game caps and the per-browser job queue, unchanged.
- **Adapted to today's code:** when a lab finishes, `cutlab.js` closes any
  browser run opened from History (`BrowserRuns.close()`), so Results and
  Replay show the lab's final deck from the server, as `onRunFinished` does.
- Cut lab and Recs run on the **server (Python) engine**, whatever
  `?engine=browser` says.
- **Recs fix (137c3c7):** opening the tab, switching mode and pressing Find
  could have two lookups in flight. Each network note then showed twice, and
  a stale reply could replace newer cards. Now only the latest reply counts.
- **Tests:** the ported `tests/sim/test_cutlab.py` and `tests/data/test_recs.py`
  pass (19). Full suite with `python -m pytest -n 4`: 3391 passed, 1 skipped,
  5 xfailed. `engine/`, `webapp/` and the Dockerfile are untouched, so no npm
  runs or image build were needed. The image already copies `mtgfish/` whole
  and installs `requests`.
- **End to end** (local `python -m mtgfish.web`, headless Chromium through
  Playwright), signed out and signed in (a freshly registered account):
  - Both tabs show in the nav.
  - Cut lab, 105-card Krenko list (`bench/decks/aggro.txt` plus 5), budget
    40 / 100: "38 fixed, 66 on trial for 61 slots, 5 to cut". It finished in
    185 to 220 s, listed the 5 cuts with their phase-2 rounds, and opened
    Results on the final deck.
  - Recs, Similar cards: 60 recommendations, colour- and legality-checked,
    from local oracle tags.
  - Recs, EDHREC: **not testable here.** This cloud environment's proxy
    blocks `json.edhrec.com` and `api.scryfall.com` (403). The page says so
    in plain notes ("could not reach EDHREC ...", "recency filter and
    upcoming cards unavailable") and does not break. Card art in the tiles
    is blank for the same reason. **Please check EDHREC mode on the live
    site**, where the server can reach both hosts.

## Next

- **Browser-engine Cut lab and Recs:** run phase 1 and phase 2 variants
  through `BrowserRuns.simulate` when `?engine=browser` is on. The scoring in
  `mtgfish/sim/cutlab.py` is pure arithmetic over per-game results and ports
  directly.

## Status

**Done and verified, apart from EDHREC mode (see above).** Stopping here.

## Questions for the auditor

- EDHREC mode and card art couldn't be reached from this sandbox. Please
  check one EDHREC query on the deployed site after the merge.
- The two-hourly "Nudge: web cloud agent (Cut lab and Recs)" scheduled task
  (trig_01KVVfb4eXjzSpPVMCPqF3RD) is still firing. Nothing is left to do on
  this job, so it can be disabled.
