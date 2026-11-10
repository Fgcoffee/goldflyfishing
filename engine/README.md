# `engine/` — the rules engine in TypeScript

The Python engine (`mtgfish/rules`) is being ported to TypeScript so simulations
run in the player's browser, in Web Workers, instead of on the server. This
package is pure TypeScript: no DOM, no Node-only APIs. The web app imports it.

Decisions already made:
- No seed-for-seed parity with the Python engine is required.
- Some translation bugs are accepted.
- The Python engine stays runnable as the reference until this one passes the
  ported tests.

```bash
cd engine
npm install
npm test           # vitest
npm run typecheck  # tsc --noEmit
```

## One shared branch

All agents work on **`claude/modest-bohr-yqrqm7`**. The auditor session
coordinates and merges into `main`.

| Area | Owner | Paths |
|---|---|---|
| Card IR: JSON schema, Python exporter, TS types | parser session | `schema/`, `engine/src/ir/`, `mtgfish/ir/`, `mtgfish/tools/ir_export.py` |
| Kernel and rules: state, zones, objects, layers, casting, stack, triggers, SBAs, combat, replacements, keywords | rules session | `engine/src/kernel/`, `engine/src/rules/` (except the two folders below) |
| Mana core, then filter matching, values and conditions (ported from `cr106_mana.py`, `mana_plan.py`, `kernel/matching.py`, `values.py`, `conditions.py`) | parser session | `engine/src/rules/mana/`, `engine/src/rules/match/` |
| AI and game runner, Web Workers, app build (Vite), serving IR JSON | web session | `engine/src/ai/`, `engine/src/sim/`, `webapp/`, `mtgfish/ui/web/`, `mtgfish/web/` |
| Test fixtures, differential tests against the Python engine, review | auditor session | `engine/test/fixtures/`, `audit/` |

**Reporting:** each agent keeps `engine/status/<agent>.md` up to date (done, next, questions for the auditor). Direct messages to the auditor are refused, so put questions there or in commit messages.

Shared files (`engine/package.json`, `engine/tsconfig.json`,
`engine/vitest.config.ts`, this README) are changed only after telling the
auditor, so two agents don't edit them at once.

### Working on the shared branch
- `git pull --rebase` before you start and before every push. Never force-push.
- Keep commits small, and touch only your own paths. If you need something in
  another area, message its owner rather than editing their files.
- Prefix commit subjects: `[ir]`, `[rules]`, `[web]`, `[audit]`.
- Before pushing, run `npm test` and `npm run typecheck` in `engine/`. If you
  touched Python, also run `python -m pytest`.
- `main` keeps serving the Python app. Nothing under `engine/` affects it until
  the web session switches the app over.

### The Python code is frozen
- No features, refactors or bug fixes, until the user says otherwise.
- Bug resolution is paused, F61 included.
- Edits from the user's collaborator may still land in the Python logic while
  the port runs. The auditor tracks them and tells the owner of the matching TS
  module, so they're carried over.

## Rules for the port

These are the places where the TS engine must differ from Python on purpose.

- **Card abilities come in as versioned JSON IR,** never parsed oracle text. The
  IR includes the already-expanded keyword abilities and the unread markers.
- **One seeded PRNG** (e.g. sfc32 or xoshiro128**), passed through the game.
  - Never use `Math.random`.
  - The lookahead's shadow game gets a cloned PRNG state, so planning never
    advances the real game's stream.
- **32-bit maths:** the game-seed derivation (Python `RunConfig.seed_for`)
  multiplies past 2^53. Use `Math.imul` and `>>> 0`.
- **Game state is plain data, or has explicit `clone()` methods.**
  `structuredClone` drops class prototypes. Python cloned games with pickle.
- **Value equality:** Python's frozen dataclasses (ObjectFilter, ManaCost,
  ManaKind, …) compare and hash by value. In TS, use canonical string keys or
  equality helpers wherever they're used as map or set keys.
- **Ordering:**
  - Use `Map` for id-keyed maps; plain objects reorder integer-like keys.
  - Sort explicitly wherever `Set` iteration order matters.
- **Integers:** Python's `//` and `%` floor toward negative infinity; JS `%`
  truncates.
- **Digest:** keep the game-log digest, using a small synchronous hash (browser
  crypto is async). A game must replay identically from its seed within this
  engine, and `verify()` checks it.
- **Port each module's tests with it.** The board-state regression tests are the
  record of every bug already fixed. The auditor also turns them into
  language-neutral JSON fixtures, run against both engines.
