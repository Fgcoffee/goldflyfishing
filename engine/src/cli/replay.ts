/**
 * `node replay.cjs < request.json > results.json`: the spot check's replay
 * CLI (bundled by `npm run build:replay`). Reads a ReplayRequest from stdin,
 * writes `{"results": [...]}` to stdout; exits 2 on a bad request.
 */

import { replayGames, type ReplayRequest } from "../sim/replay";

// Node's globals, declared here: the engine's tsconfig has no Node types.
declare const process: {
  stdin: { setEncoding(e: string): void; on(event: string, cb: (chunk?: string) => void): void };
  stdout: { write(s: string): void };
  stderr: { write(s: string): void };
  exitCode: number;
};

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk ?? "";
});
process.stdin.on("end", () => {
  let request: ReplayRequest;
  try {
    request = JSON.parse(input) as ReplayRequest;
  } catch (error) {
    process.stderr.write(`bad request: ${String(error)}\n`);
    process.exitCode = 2;
    return;
  }
  try {
    process.stdout.write(JSON.stringify({ results: replayGames(request) }));
  } catch (error) {
    process.stderr.write(`${String(error)}\n`);
    process.exitCode = 2;
  }
});
