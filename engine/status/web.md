# web session: status

Update this file with every push: what landed, what's next, and any
**Questions for the auditor** (the auditor reads it, and every commit
message, at each check-in; direct messages to the auditor are refused).

Owner paths: `webapp/`, `engine/src/ai/`, `engine/src/sim/`, `mtgfish/web/`,
`mtgfish/ui/web/`, `Dockerfile`.

## Done

- **Vite + Web Worker harness** (`webapp/`, merged in main 4af8660). Module
  workers import the engine through an alias to its TS source. `EngineClient`
  uses id-matched promises.
- **anyio BlockingPortal filter** for `tests/web` (af94441).
- **Card IR serving:**
  - `mtgfish/web/ir.py`:
    - `GET /api/ir` reports whether IR is exported, plus its version,
      snapshot and card count.
    - `POST /api/ir/decks {"decklists": [...]}` (up to 4) resolves the decks
      through the card database (every spelling a decklist uses). It returns
      each deck's commanders and cards by oracle id, plus `unresolved` and
      `missing_ir` (cards newer than the export). It also returns every
      card's `card_hash` and URL.
    - `GET /ir/v1/<oracle_id>.json?h=<card_hash>` serves one card document,
      gzipped. When `h` matches the export, it's served immutable for a
      year, so the browser's HTTP cache keeps it until the text changes.
      Otherwise it's `no-cache` with an ETag (304 on a match).
    - Rate limits: 60 deck lookups and 3000 card files per address per
      minute.
    - The 7 MB name index is never sent, and isn't even kept in memory.
    - The index is reloaded when the export changes.
  - IR directory: `MTGFISH_IR_DIR`, default `<data root>/ir/v1`.
  - Dockerfile: the image runs `ir_export export` at build time
    (`/app/data/ir/v1`).
  - `webapp/src/ir.ts` (`IrClient`):
    - resolves the decks, then fetches only cards it doesn't already hold
      at that hash (8 at a time);
    - decodes each card with `engine/src/ir` `readCard`, which refuses other
      IR versions;
    - checks each card's oracle id and hash against the manifest.
  - The harness page has a deck box to try it. The Vite dev and preview
    servers proxy `/api` and `/ir` to the Python server (`GOLDFLY_BACKEND`,
    default `http://127.0.0.1:8041`).
  - Measured locally on the four tester decks: 261 cards, resolved in
    0.08 s, fetched in 0.37 s, 182 kB gzipped. A second load downloads
    nothing.

- **Run protocol with a stub engine:**
  - `engine/src/sim`:
    - `records.ts` holds `GameRecord` and `CrashedGame`, mirroring Python's.
      Per-turn series and removal are left out until the kernel can fill
      them.
    - `runner.ts`:
      - `RunSetup` is the decks by oracle id, the cards' IR, `runSeed` and
        `maxRounds`.
      - `GameEngine` is `play(setup, index, seed) -> GameRecord`.
      - `runGames(engine, setup, start, end, hooks)` gives each game
        `seedFor(runSeed, index)`. A game that throws is left out and
        reported (as Python's #35 does), and the run can stop and yield
        between games.
      - `splitRange` and `mergeResults` split a run across workers and
        merge it back.
    - `stub.ts` is a stand-in engine that plays no Magic but uses only the
      game's seeded PRNG, so a seed always gives the same record and
      digest. It is swapped for the real engine without changing anything
      else.
  - `webapp/src/worker`: the protocol is now
    `ping→pong`, `run→progress*→result` and `cancel`. A cancelled run still
    answers, with `complete: false`.
  - `webapp/src/pool.ts` (`WorkerPool`) splits a run over N workers
    (default: cores − 1) and reports overall progress. One worker and
    several give identical results; that is tested.
  - Checked in the browser on the four tester decks: 1000 stub games on 23
    workers in 53 ms, 0 crashed, and a second run gives an identical digest
    for every game.

## Next

- Swap the stub for the real engine once the kernel can set up and play a
  game. What I need from the rules session: a `play(setup, index, seed)`
  entry point, or the pieces to build one. `RunSetup` carries each deck by
  oracle id plus the cards' IR documents.
- The AI (`engine/src/ai`): SimpleAgent and lookahead, once there's game
  state to decide on.
- The report: a TS port of `mtgfish/sim/stats.py` `summarize` over
  `GameRecord`s, so the Results page can read browser runs.

## Questions for the auditor

- The parser session's `mtgfish/ir/store.py` `IRStore.deck(names)` (e7c8c7d)
  landed while I was building `mtgfish/web/ir.py`. Mine resolves decklists
  through the card database and serves the exported files as they are. A
  card newer than the export is reported in `missing_ir`, not re-parsed.
  Should `/api/ir/decks` switch to `IRStore` so stale or missing cards get
  a live re-parse? If so, I'll do it next. The catch is that a live parse
  costs CPU per request on the 0.15-CPU Render box, and immutable caching
  then needs care, because a live document has a hash the export doesn't.

- `mtgfish/tools/ir_export.py` writes `index.json` with `write_text` and no
  encoding, so on Windows (cp1252) `export` crashes with UnicodeEncodeError
  (a card name with "ō"). The card files themselves pass `encoding="utf-8"`.
  Linux and Docker are unaffected. The workaround is `PYTHONUTF8=1`. This is
  for the parser session.
- The Docker image build with the IR export step hasn't been run locally.
  It's checked on the next Render deploy.
