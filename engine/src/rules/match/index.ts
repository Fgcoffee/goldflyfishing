/**
 * Matching, values and conditions: the IR's filters, counts and conditions
 * evaluated against the game. Importing this installs them into
 * kernel/hooks.ts for the layers and resolution to call.
 */
import { installMatchHooks } from "../../kernel/hooks";
import { holds } from "./conditions";
import { defendingPlayers, find, matches, resolvePlayers, valueSubjects } from "./matching";
import { evaluate } from "./values";

export * from "./matching";
export * from "./values";
export * from "./conditions";

/** Install match/'s functions into kernel/hooks.ts. Done on import; callable again (tests). */
export function installMatch(): void {
  installMatchHooks({
    matches: (game, obj, spec, context) => matches(game, obj, spec, context),
    find: (game, spec, context) => find(game, spec, context),
    evaluate: (game, value, context) => evaluate(game, value, context),
    holds: (game, condition, context) => holds(game, condition, context),
    valueSubjects: (value) => valueSubjects(value),
    resolvePlayers: (game, spec, context) => resolvePlayers(game, spec, context),
    defendingPlayers: (game, source) => defendingPlayers(game, source),
  });
}

installMatch();
