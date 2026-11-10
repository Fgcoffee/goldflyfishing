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
- `src/main.ts` + `index.html`: the current harness page. It starts one worker
  per spare core and pings each.

The engine is imported from its TypeScript source through an alias
(`vite.config.ts`, `tsconfig.json` paths), so it needs no build step or
package entry point yet.

Next: a `run` request (decklists + IR + seed + game range in, game records
out) once the kernel and runner exist, and serving card IR JSON for just the
cards in the decks being simulated.

The live Python app (`mtgfish/web`, `mtgfish/ui/web`) is untouched and stays
on `main` until the switch-over.
