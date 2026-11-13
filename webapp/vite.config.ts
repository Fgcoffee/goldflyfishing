import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The engine is imported from its TypeScript source rather than as a built
// package, so the app always runs whatever is on the branch and the engine
// needs no build step or package entry point of its own yet.
const engineFile = (path: string) => fileURLToPath(new URL(`../engine/src/${path}`, import.meta.url));

/**
 * A fingerprint of the engine source the bundle is built from: every .ts
 * file under engine/src except tests, by path and content. Stored with each
 * browser run, so the server can replay it with the same engine.
 */
function engineBuild(): string {
  const root = engineFile("");
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.name.endsWith(".ts") && !entry.name.endsWith(".test.ts")) files.push(path);
    }
  };
  walk(root);
  const hash = createHash("sha256");
  for (const path of files.sort()) {
    hash.update(path.slice(root.length).replaceAll("\\", "/"));
    hash.update("\0");
    // Line endings normalised, so a Windows checkout builds the same fingerprint.
    hash.update(readFileSync(path, "utf8").replaceAll("\r\n", "\n"));
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

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
