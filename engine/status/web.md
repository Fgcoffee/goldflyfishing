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
      `live_ir` (see the IRStore entry below). It also returns every card's
      `card_hash` and URL, or the document inline when parsed live.
    - `GET /ir/v1/<oracle_id>.json?h=<card_hash>&b=<build>` serves one card
      document, gzipped. When both match the export, it's served immutable
      for a year. Otherwise it's `no-cache` with an ETag (304 on a match).
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

- **`/api/ir/decks` now uses the parser's `IRStore`,** under the auditor's
  three conditions:
  1. `IRStore.card()` serves the exported file when its hash is current, and
     parses live otherwise. Each request that parses live logs a warning with
     the count, the running total (`IRStore.live_parses`) and the card names,
     so a stale image shows up in the Render logs.
  2. A live-parsed card comes inline in the manifest (`document`, with no
     `url`), so it is never served as a file or cached. `IrClient` uses it
     for that run only and never holds it. Only exported files are immutable.
  3. Every exported card URL carries the export's build fingerprint
     (`?h=<card_hash>&b=<build>`). It is immutable only when both match, and
     `IrClient` keys its cache on hash + build.
  - `build` is the first 16 hex digits of `index.json` `code` (the parser's
    `code_fingerprint()`, fe93356), falling back to `built_at` for an older
    export. An export written by other parser code is served entirely live
    by `IRStore`; that is tested through the endpoint.
  - The per-deck field `missing_ir` is renamed `live_ir`: the cards the
    server parsed live.
- **End-to-end harness page** (`webapp/index.html`, `src/main.ts`):
  - You paste decklists (yours first, then up to three opponents), set the
    games, seed and turn cap, then Run or Cancel.
  - It shows progress, then a per-seat result table: wins, win rate, average
    win turn, commander cast and average turn, mulligans. Notes list unknown
    names, live-parsed cards and crashed games.
  - A replay check re-runs the first 100 games and compares digests.
  - `engine/src/sim/summary.ts` `summarize` is the first slice of Python's
    stats: per-seat numbers and stalls.
  - Swapping the stub for the real engine is the one `ENGINES` entry in
    `webapp/src/worker/handle.ts`.
  - Checked in the browser on the four tester decks:
    - 5000 stub games on 23 workers in 0.23 s, and the replay check passed.
    - Cancel stopped a 100,000-game run at 26,210 with `complete: false`.
    - The second run downloaded 0 cards.

- **IR v2.** `mtgfish/web/ir.py` takes `IR_VERSION` from `mtgfish.ir.codec`
  (now 2), so the routes are `/ir/v2/...` and the default directory is
  `ir/v2`. The Dockerfile's export check follows the same constant. Card
  documents, including the printed card data (ff6dec3), are served as the
  exporter wrote them. Checked in the browser on a fresh v2 export: 261
  cards, none parsed live.
- **Run report** (`engine/src/sim/report.ts` `buildReport`): ports
  `stats.py` `summarize` for what a `GameRecord` carries:
  - outcomes, win and loss reasons, and `byWinRound`;
  - the commander histogram, with the games behind each bar;
  - card impact (drawn vs not drawn, once per game), its 95% margin, and
    `topCards`/`worstCards` past `MIN_SAMPLE`.

  It is plain JSON-friendly data. The harness shows the top 10 cards.
- **Every browser run is sent back and stored:**
  - Table `browser_runs` (migration `0004_browser_runs`): the run seed, game
    range, hero, turn cap, `complete`, the engine build, IR build and
    snapshot, the decks as resolved (oracle ids, card hashes, seat order),
    and a gzipped payload holding every game record (with its seed and
    digest) and every crashed game (with seed, error and stack).
    `spot_check` starts as "pending".
  - `POST /api/browser-runs` takes gzipped JSON, capped at 8 MB compressed
    and 80 MB unpacked (a zip bomb stops at the cap). It is limited to 30
    uploads per minute per address. It checks every game's seed against
    `seed_for(run_seed, index)`, ranges, duplicates, sizes, and that a
    `complete` run holds every game. A run that fails is refused whole. A
    cancelled run uploads what it finished.
  - Signed in, a run belongs to the account: `GET /api/browser-runs` lists
    them, it is in `/auth/account/export`, and it is deleted with the
    account. Signed out, it is stored with no link to anyone and purged
    after 365 days by the existing 6-hourly purge.
  - `webapp/src/upload.ts` builds the upload, gzips it with
    `CompressionStream`, and the harness sends it after every run.
  - The engine build is a sha256 of `engine/src/**/*.ts` (not tests, line
    endings normalised), baked into the bundle by `vite.config.ts` as
    `__ENGINE_BUILD__`.
  - `/privacy` now says that decks and results of browser runs are sent and
    kept, why (legitimate interests: checking results, finding engine
    faults), and for how long. `TERMS_VERSION` is now 2026-11-13.
  - Checked in the browser: 5000 stub games uploaded and stored complete,
    678 kB gzipped, with every field present in the database.

- **Engine build gate** (the auditor's answer: check at upload time):
  - `mtgfish/web/engine_build.py` fingerprints `engine/src` exactly as
    `webapp/engine-build.mjs` does, and the Vite config imports that module.
    A test runs the Node script and checks the two agree.
  - `POST /api/browser-runs` refuses a run from any other engine build with
    409 and `"reload": true`. The page then says to reload and run again.
  - `/api/ir` reports the server's `engine_build`.
  - The Docker image copies `engine/src` so the server can fingerprint it.
    `MTGFISH_ENGINE_BUILD` overrides the fingerprint.

## Next

- **Server-side spot check.** A run is only accepted from the server's own
  engine build, so the server always has the right engine to replay it. A
  run that passes is marked "ok" for good, with its build recorded. Plan:
  - a Node build of `engine/src` with a small replay CLI (esbuild);
  - Node in the Docker image;
  - a background job that replays a random sample of each pending run,
    compares digests, and marks the run "ok" or "mismatch".
- Swap the stub for the real engine once the kernel can play: the rules
  session's `play(setup, index, seed)`.
- The AI (`engine/src/ai`), once there's game state to decide on.
- The rest of the report (per-turn series, removal), once records carry
  them.

## Questions for the auditor

- The Docker image build with the IR export step hasn't been run locally.
  It's checked on the next Render deploy.
- Resolved: engine builds are checked at upload time; "ok" is permanent and
  stored with its build.
- Resolved: switching to `IRStore` (done above); the build fingerprint uses
  the parser's `code` key; the Windows encoding bug was fixed in fe93356.
