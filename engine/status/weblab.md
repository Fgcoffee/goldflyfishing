# weblab session: status (Cut lab and Recs restore)

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
