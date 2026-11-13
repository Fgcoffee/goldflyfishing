/**
 * The fingerprint of the engine source: every .ts file under engine/src
 * except tests, by relative POSIX path and content (CRLF -> LF), sorted by
 * path, sha256, first 16 hex digits.
 *
 * Must match mtgfish/web/engine_build.py byte for byte: the server refuses a
 * browser run from any other build. `node webapp/engine-build.mjs` prints it,
 * which a Python test uses to check the two agree.
 */
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const ENGINE_SRC = fileURLToPath(new URL("../engine/src/", import.meta.url));

export function engineBuild(root = ENGINE_SRC) {
  const files = [];
  const walk = (dir, rel) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      const name = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(path, name);
      else if (entry.name.endsWith(".ts") && !entry.name.endsWith(".test.ts")) files.push([name, path]);
    }
  };
  walk(root, "");
  files.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const hash = createHash("sha256");
  for (const [name, path] of files) {
    hash.update(name);
    hash.update("\0");
    hash.update(readFileSync(path, "utf8").replaceAll("\r\n", "\n"));
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  console.log(engineBuild());
}
