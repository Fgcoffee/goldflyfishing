# `webapp/`: the browser app

Runs `@goldfly/engine` (`../engine`) in Web Workers, so games are simulated on
the player's machine instead of the server. Owned by the web session; see
`../engine/README.md` for the shared-branch rules.

```bash
cd webapp
npm install
npm test           # vitest: worker protocol and client
npm run typecheck
npm run build      # typecheck, then bundle to dist/
npm run preview    # serve dist/ on http://localhost:5181
npm run dev        # dev server on http://localhost:5180
```

On this machine, cmd can't find `node` by its bare name, so `npm install`
(esbuild's install script) fails. Put `C:\Program Files\nodejs` first on PATH
for the shell first.

## How it fits together

- `src/worker/protocol.ts`: the messages. Every request has an `id` that its
  reply echoes. Only plain data crosses, never class instances.
- `src/worker/handle.ts`: what the worker does with a message, as a plain
  function, so it's testable without a browser.
- `src/worker/sim.worker.ts`: the worker entry point. Engine exceptions come
  back as `error` replies, so a promise is never left waiting.
- `src/client.ts`: `EngineClient`, the page's side: requests in, promises out.
- `src/pool.ts`: `WorkerPool`, which splits a run's games across workers
  and merges the results in index order.
- `src/ir.ts`: `IrClient`. It resolves decks on the server
  (`POST /api/ir/decks`), then fetches only the card files it doesn't hold
  at that hash.
- `src/main.ts` + `index.html`: the current harness page. It pings every
  worker, loads card IR for pasted decks, and plays the games, then replays the
  first 100 to check they come out identical.

The engine is imported from its TypeScript source through aliases
(`@goldfly/engine`, `/ir`, `/sim`; see `vite.config.ts` and the
`tsconfig.json` paths), so it needs no build step or package entry point.

The dev and preview servers proxy `/api` and `/ir` to the Python server
(`GOLDFLY_BACKEND`, default `http://127.0.0.1:8041`), which needs an IR
export: `python -m mtgfish.tools.ir_export export` (set `MTGFISH_IR_DIR`
to serve one from elsewhere).

Games are played by `engine/src/sim` (`runGames`) with `ENGINES[DEFAULT_ENGINE]`
(`engine/src/sim/engines.ts`): the real rules engine, the simple bot in every
seat. The stub engine is still there by name, for the worker plumbing tests.

The live Python app (`mtgfish/web`, `mtgfish/ui/web`) is untouched and stays
on `main` until the switch-over.
