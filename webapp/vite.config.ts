import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
// The engine's build fingerprint, stored with each browser run. The server
// computes the same one (mtgfish/web/engine_build.py) and refuses others.
import { engineBuild } from "./engine-build.mjs";

// The engine is imported from its TypeScript source rather than as a built
// package, so the app always runs whatever is on the branch and the engine
// needs no build step or package entry point of its own yet.
const engineFile = (path: string) => fileURLToPath(new URL(`../engine/src/${path}`, import.meta.url));

// The Python web server, for /api and /ir during `npm run dev`.
const backend = process.env.GOLDFLY_BACKEND ?? "http://127.0.0.1:8041";

// The server refuses a POST whose Origin isn't its own. Through this proxy
// the page's origin is Vite's, so the proxy presents itself as the backend.
// Development only: in production the page and the API share one origin.
const toBackend = {
  target: backend,
  changeOrigin: true,
  configure: (proxy: { on: (event: "proxyReq", fn: (req: { setHeader: (k: string, v: string) => void; getHeader: (k: string) => unknown }) => void) => void }) => {
    proxy.on("proxyReq", (req) => {
      if (req.getHeader("origin") !== undefined) req.setHeader("origin", backend);
    });
  },
};
const proxy = { "/api": toBackend, "/ir": toBackend };

export default defineConfig({
  define: { __ENGINE_BUILD__: JSON.stringify(engineBuild()) },
  resolve: {
    // Most specific first: "@goldfly/engine" would otherwise also match
    // "@goldfly/engine/ir" as a prefix.
    alias: [
      { find: /^@goldfly\/engine\/ir$/, replacement: engineFile("ir/index.ts") },
      { find: /^@goldfly\/engine\/sim$/, replacement: engineFile("sim/index.ts") },
      { find: /^@goldfly\/engine$/, replacement: engineFile("index.ts") },
    ],
  },
  worker: {
    // ES module workers: the worker imports the engine like any other module.
    format: "es",
  },
  server: {
    port: 5180,
    strictPort: true,
    fs: { allow: [".."] },
    proxy,
  },
  preview: {
    port: 5181,
    strictPort: true,
    proxy,
  },
  test: {
    include: ["test/**/*.test.ts", "src/**/*.test.ts"],
  },
});
