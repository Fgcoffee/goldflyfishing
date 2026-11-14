/**
 * Rules a sandbox bench may switch off. Ported from
 * mtgfish/rules/kernel/relaxations.py. A real run only ever uses STRICT, where
 * every flag is false; the bench uses BENCH so a hand-built board without a
 * library or with 1 life is still usable.
 */

export interface Relaxations {
  /** CR 704.5a-c, 903.10: nobody loses the game. */
  readonly playersCannotLose: boolean;
  /** CR 704.5b: drawing from an empty library does nothing. */
  readonly drawsFromAnEmptyLibraryDoNothing: boolean;
  /** CR 500.4: mana stays in the pool across steps and phases. */
  readonly manaPoolsPersist: boolean;
}

export const STRICT: Relaxations = Object.freeze({
  playersCannotLose: false,
  drawsFromAnEmptyLibraryDoNothing: false,
  manaPoolsPersist: false,
});

export const BENCH: Relaxations = Object.freeze({
  playersCannotLose: true,
  drawsFromAnEmptyLibraryDoNothing: true,
  manaPoolsPersist: true,
});

export const RELAXATION_DESCRIPTIONS: Readonly<Record<keyof Relaxations, string>> = {
  playersCannotLose:
    "Nobody loses the game - not to zero life, an empty library, poison or commander damage (CR 704.5a-c, 903.10)",
  drawsFromAnEmptyLibraryDoNothing:
    "Drawing from an empty library does nothing instead of losing the game (CR 704.5b), so a board with no deck behind it still works",
  manaPoolsPersist:
    "Mana stays in the pool across steps and phases (CR 500.4), so mana handed over for a test survives the next advance",
};

export function anyRelaxationActive(r: Relaxations): boolean {
  return r.playersCannotLose || r.drawsFromAnEmptyLibraryDoNothing || r.manaPoolsPersist;
}
