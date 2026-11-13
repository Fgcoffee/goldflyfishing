# Roadmap

Single list of what's being built, split by agent. **Each agent ticks off its
own items** (`- [x]`) in the commit that finishes them, and adds the commit
hash. Edit only your own section: `git pull --rebase` first, small commits.
The auditor reviews ticked items and merges into `main` at milestones.

Owners:
- **rules:** rules session.
- **parser:** parser session.
- **web:** web builder session.
- **auditor:** auditor session, which coordinates the three.

The paths each owns are in [`engine/README.md`](engine/README.md).

## Milestones (what the user is told about)

1. [x] Game state and card characteristics work in TypeScript
2. [ ] Casting spells and paying mana work
3. [ ] The stack, triggers and combat work
4. [ ] A first full game plays to the end in TypeScript
5. [ ] Games run in the browser, in Web Workers
6. [ ] The website runs its simulations in the browser instead of on the server

---

## Phase 1: the TypeScript port (current)

Python is frozen. Magic rules bugs are ported as they are, marked
`// KNOWN: Fxx`, and fixed after the port. Only crashes, broken builds or tests,
and games that don't replay from their seed are fixed now.

### rules
- [x] Kernel foundations: enums, ids, seeded PRNG with `seedFor`, log digest (d7a1822)
- [x] Game state with `cloneGame`, players, mana pool, game objects, card shape (fdf9421, 6d3d451)
- [x] Layer system and characteristics on the IR (d73eadc)
- [x] Event kinds (d86e864); hook seam for `rules/match` (6d3cd35)
- [x] Read IR enums as numbers (IR v2). Switch first; the parser then re-lands v2 ("Read IR enums as numbers or strings" commit)
- [x] Event bus: emit, observers, per-turn records ("Event bus" commit)
- [x] Moving objects between zones (`move_object`), with replacement and trigger hooks ("Moving objects" commit)
- [x] Turn structure (CR 500-514): steps, untap/upkeep/draw, priority loop ("Turn structure" commit)
- [ ] Casting spells (CR 601) and activating abilities (602), using the parser's mana code
- [ ] Resolving the stack: effect executors (`resolve.py`)
- [ ] Triggered abilities (CR 603), including look-back
- [ ] State-based actions (CR 704)
- [ ] Combat (CR 506-511): attack and block requirements, costs, damage
- [ ] Replacement and prevention effects (CR 614-616)
- [ ] Keyword runtime (CR 702)
- [ ] Copies, faces and tokens (CR 707, 711, 712)
- [ ] Commander rules (CR 903): command zone, tax, commander damage
- [ ] Game setup (CR 103): shuffles, mulligans
- [ ] `play(setup, index, seed)` entry point for the runner, agreed with web
- [ ] Engine build fingerprint (version plus commit, stamped at build), recorded in every game record. `play` runs unchanged in Node, so the server can replay uploaded games
- [ ] Each module's Python board tests ported as Vitest tests

### parser
- [x] IR v1: JSON schema, codec, exporter; every face round-trips (70c670b)
- [x] TS types, defaults and reader in `engine/src/ir` (faff6f4)
- [x] Bench-deck samples (5c0016d); `IRStore` for serving decks (e7c8c7d)
- [x] IR v2, enums as numbers: re-land once rules has switched; verify every face; refresh samples (b09ae5e)
- [x] Exporter writes every file as UTF-8 (it crashes on Windows today) (fe93356)
- [x] Parser-code fingerprint in `index.json`; `IRStore` treats a stale export as stale (fe93356)
- [x] Printed card data (name, mana cost, type line, P/T, colours: `CardDef`/`FaceDef`) exported with each card's IR, matching `kernel/card.ts` (ff6dec3)
- [x] Mana core: port `cr106_mana.py` to `engine/src/rules/mana/`, with its tests (0719d8e)
- [ ] Mana planning: port `mana_plan.py`, keeping the F61 guard. Core and guard done (26c37d7); the game-side host (`ManaPlanHost`) waits on the rules session's casting
- [x] Matching, values and conditions: port `matching.py`, `values.py` and `conditions.py` to `engine/src/rules/match/` (d20edb7)

### web
- [x] Vite app and Web Worker harness (4bedd13)
- [x] IR serving endpoint and `IrClient` (249dc1b)
- [x] Run protocol and `WorkerPool`, with a stub engine (2195208)
- [ ] `/api/ir/decks` uses `IRStore`. Live-parsed cards aren't cached as immutable; cache keys include the build fingerprint
- [ ] End-to-end harness page: decklist in, IR fetched, stub games in workers, results table out
- [ ] AI port: SimpleAgent and lookahead (`engine/src/ai`)
- [ ] Report and stats port (`stats.py` summarize) in TS
- [ ] Swap the stub for the real engine; games run in the browser
- [ ] Results and replay screens read browser runs
- [ ] Switch the website from server simulations to browser simulations
- [ ] **Every browser run is sent back to the server and stored.** Required before the switch-over. Each run records:
  - the run seed (master seed), the game range, and each game's own seed and log digest;
  - the engine build fingerprint, the IR build fingerprint and the card-data snapshot;
  - the decks as resolved: oracle ids and card hashes, seat order, the hero seat;
  - the turn cap;
  - every game record, and every crashed game with its seed, error and stack;
  - whether the run finished.

  A cancelled or partial run uploads what it completed. Uploads are compressed. The privacy page says that decklists and simulation results are sent to and kept by the server.
- [ ] **Server-side spot check of uploaded runs.** The server replays a random sample of each upload in Node with the same engine build, and compares log digests. Mismatches, from a tampered or broken client, are flagged and kept out of any shared statistics (brackets, card impact).

### auditor
- [x] Shared branch, ownership, status files, this roadmap
- [x] IR v1 verified: all 42,027 faces round-trip with 0 problems
- [ ] Language-neutral test fixtures from the Python board tests, with a runner checking both engines
- [ ] Review each module as it lands; run the bench decks on the TS engine once it plays
- [ ] Merge the shared branch into `main` at each milestone
- [ ] Check that uploaded browser runs replay identically: a sample across browsers, so the same seed gives the same digest in Chrome, Firefox and Safari, and in Node

---

## Phase 2: after the TypeScript conversion

Not started until Phase 1 is finished.

### web
- [ ] **Restore the Cut lab and Recs tabs** on the TS engine. They were taken off the live Render page for now.
- [ ] **Deck slot picker:** a dropdown in the deck section to choose which seat (1-4) a deck goes into.
- [ ] **Archidekt linking that doesn't look like data harvesting.** Today the site asks for your Archidekt username and password and signs in for you, because Archidekt has no API or OAuth. People will reasonably assume we're collecting their data. Replace it with:
  - import by public deck link, or by username for public decks only, with no Archidekt password at all;
  - a plain statement of what is fetched (only the decks you pick), that nothing else is read, and that nothing is stored beyond the decklist you save;
  - one-click deletion of any imported deck;
  - if signed-in import of private decks is ever kept, make it explicit opt-in, never store the token, and say so on screen.

### parser and web (bracket estimates)
- [ ] **Estimated bracket** from the decklist alone, similar to Archidekt's Commander Brackets. Count:
  - Game Changers;
  - tutors;
  - extra turns;
  - mass land denial;
  - known two-card combos.

  Show the bracket with the reasons that drove it. Card classification: parser. Screen: web.
- [ ] **Actual bracket** from simulations, once a deck has been played enough for a reliable win turn. It comes from win turn and win rate against reference decks, with a confidence level and the number of games it's based on. Shown next to the estimate, flagged when they disagree. Stats: web. Reference decks and sanity checks: auditor.

### rules and parser (deferred rules fixes)
- [ ] Fix the Magic rules findings deferred during the port, once games can be watched. All are logged in `audit/AUDIT_LOG.md`, e.g.:
  - F28 overload;
  - F37 Toxic Deluge;
  - F49 spot-check cards;
  - karoo lands always sacrificed;
  - the F61 reproduction.
