/** Messages between the site bundle and its replay worker. Plain data only. */

import type { RunSetup } from "@goldfly/engine/sim";

export type ReplayRequest =
  | { readonly type: "replay"; readonly id: number; readonly setup: RunSetup; readonly index: number; readonly seed: number; readonly hero: number }
  | { readonly type: "board"; readonly id: number; readonly index: number };

export interface ReplayReply {
  readonly id: number;
  readonly payload: Record<string, unknown>;
}
