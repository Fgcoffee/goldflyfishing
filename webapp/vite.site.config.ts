import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
// The engine build the server accepts runs from (mtgfish/web/engine_build.py
// computes the same). Without it the bundle says "dev" and every upload from
// the site is refused as coming from another engine.
import { engineBuild } from "./engine-build.mjs";

// The engine bundle for the website's Results and Replay screens
// (src/site/entry.ts), built into the Python site's static folder so the
// server serves it beside the page: mtgfish/ui/web/engine/goldfly-site.js
// and its replay worker. Generated; not committed (see .gitignore).
const engineFile = (path: string) => fileURLToPath(new URL(`../engine/src/${path}`, import.meta.url));

export default defineConfig({
  // Relative, so the worker is found beside goldfly-site.js wherever the
  // server mounts it (/engine/), not at the site root.
  base: "./",
  define: { __ENGINE_BUILD__: JSON.stringify(engineBuild()) },
  resolve: {
    alias: [
      { find: /^@goldfly\/engine\/ir$/, replacement: engineFile("ir/index.ts") },
      { find: /^@goldfly\/engine\/sim$/, replacement: engineFile("sim/index.ts") },
      { find: /^@goldfly\/engine$/, replacement: engineFile("index.ts") },
    ],
  },
  worker: { format: "es" },
  build: {
    outDir: fileURLToPath(new URL("../mtgfish/ui/web/engine/", import.meta.url)),
    emptyOutDir: true,
    lib: {
      entry: fileURLToPath(new URL("src/site/entry.ts", import.meta.url)),
      formats: ["es"],
      fileName: () => "goldfly-site.js",
    },
  },
});
