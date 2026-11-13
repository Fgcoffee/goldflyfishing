/**
 * The seam between the rules the rules session ports (layers, casting,
 * resolution) and the matching, values and conditions the parser session
 * ports in rules/match/ (from kernel/matching.py, values.py, conditions.py).
 *
 * Layers need "does this object match this filter" before match/ exists, and
 * match/ needs characteristics from layers. Rather than import each other,
 * both go through these slots: match/ installs its functions with
 * `installMatchHooks`; until then a call says plainly that it is missing.
 * The signatures follow the Python functions, with keyword arguments as an
 * options object.
 */

import type { Condition, ObjectFilter, PlayerFilter, Value } from "../ir";
import type { Characteristics } from "../rules/cr200_parts_of_a_card/characteristics";
import type { Game } from "./game";
import type { GameObject } from "./gameobject";
import type { ObjectId, PlayerId } from "./ids";

export interface MatchContext {
  /** The object whose ability is asking ("this", "other"). */
  readonly source?: ObjectId;
  /** Who "you" is. */
  readonly controller?: PlayerId;
}

export interface MatchHooks {
  /** Python `matching.matches(game, obj, spec, source=, controller=, ...)`. */
  matches(
    game: Game,
    obj: GameObject,
    spec: ObjectFilter,
    context: MatchContext & {
      allowStale?: boolean;
      /** The layer system's in-progress characteristics for `obj`. */
      override?: Characteristics;
    },
  ): boolean;
  /** Python `matching.find(game, spec, source=, controller=)`. */
  find(game: Game, spec: ObjectFilter, context: MatchContext): GameObject[];
  /** Python `values.evaluate(game, value, source=, controller=, ...)`. */
  evaluate(game: Game, value: Value, context: MatchContext & { xValue?: number }): number;
  /** Python `conditions.holds(game, condition, source=, controller=, ...)`. */
  holds(game: Game, condition: Condition, context: MatchContext): boolean;
  /**
   * Whether the last top-level `evaluate` produced an undeterminable number
   * (CR 107.2, 903.4f; Python's `Undeterminable`). match/values.ts has it as
   * `wasUndeterminable`.
   */
  wasUndeterminable(): boolean;
  /** Python `matching.resolve_players(game, spec, controller=, source=)`, APNAP order. */
  resolvePlayers(game: Game, spec: PlayerFilter, context: MatchContext): PlayerId[];
  /** Python `matching._defending_players(game, source)` (CR 506.2). */
  defendingPlayers(game: Game, source: ObjectId): PlayerId[];
  /**
   * Python `matching.value_subjects(value)`: whether the value reads the
   * affected object ("its power"), the source, and the third flag as there.
   */
  valueSubjects(value: Value): [boolean, boolean, boolean];
}

function missing(name: string): never {
  throw new Error(`rules/match has not installed ${name} yet (see kernel/hooks.ts)`);
}

let HOOKS: MatchHooks = {
  matches: () => missing("matches"),
  find: () => missing("find"),
  evaluate: () => missing("evaluate"),
  holds: () => missing("holds"),
  valueSubjects: () => missing("valueSubjects"),
  resolvePlayers: () => missing("resolvePlayers"),
  wasUndeterminable: () => false,
  defendingPlayers: () => missing("defendingPlayers"),
};

export function installMatchHooks(hooks: Partial<MatchHooks>): void {
  HOOKS = { ...HOOKS, ...hooks };
}

export function matchHooks(): MatchHooks {
  return HOOKS;
}
