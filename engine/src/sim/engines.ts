/**
 * The engines a run can be played with, by name, and which one the page
 * uses. Kept here, inside engine/src, so the build fingerprint (a hash of
 * engine/src) covers the choice: the server's spot check replays an uploaded
 * run with the engine its fingerprint names, and that is only sound if the
 * page and the replay pick the engine the same way.
 *
 * The webapp worker plays `ENGINES[request.engine]`, and the page asks for
 * DEFAULT_ENGINE: the real rules engine, seating SimpleAgent.
 */

import { rulesEngine } from "../rules/play";
import type { GameEngine } from "./runner";
import { stubEngine } from "./stub";

export const ENGINES = { stub: stubEngine, rules: rulesEngine } as const satisfies Record<string, GameEngine>;
export type EngineName = keyof typeof ENGINES;

/** What the page plays today. */
export const DEFAULT_ENGINE: EngineName = "rules";
