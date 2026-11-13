/**
 * Numbers (CR 107.1b-c). Ported from mtgfish/rules/cr100_game_concepts/
 * cr107_numbers.py. The CR 107.2 undeterminable marker is a flag beside the
 * number here (match/'s `wasUndeterminable`, via kernel/hooks.ts).
 */

/** CR 107.1b: an amount an effect does is never negative. */
export const effectResult = (amount: number): number => Math.max(0, amount);

/** CR 107.1b-c: a number a player chooses is zero or more. */
export const chosenNumber = (amount: number): number => Math.max(0, amount);
