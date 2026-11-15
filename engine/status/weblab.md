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
- Tests: `tests/sim/test_cutlab.py` and `tests/data/test_recs.py`, 19 passing.

## Next

- **Browser-engine Cut lab and Recs:** run phase 1 and phase 2 variants
  through `BrowserRuns.simulate` when `?engine=browser` is on. The scoring in
  `mtgfish/sim/cutlab.py` is pure arithmetic over per-game results and ports
  directly.

## Questions for the auditor

- None yet.
