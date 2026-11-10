import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The engine is imported from its TypeScript source rather than as a built
// package, so the app always runs whatever is on the branch and the engine
// needs no build step or package entry point of its own yet.
const engine = fileURLToPath(new URL("../engine/src/index.ts", import.meta.url));

export default defineConfig({
  resolve: {
    alias: { "@goldfly/engine": engine },
  },
  worker: {
    // ES module workers: the worker imports the engine like any other module.
    format: "es",
  },
  server: {
    port: 5180,
    strictPort: true,
    fs: { allow: [".."] },
  },
  preview: {
    port: 5181,
    strictPort: true,
  },
  test: {
    include: ["test/**/*.test.ts", "src/**/*.test.ts"],
  },
});
