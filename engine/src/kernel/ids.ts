/**
 * Identity primitives. Ported from mtgfish/rules/kernel/ids.py.
 *
 * ObjectId identifies an object (CR 109.1), not a card: a zone change makes a
 * new object with a fresh id (CR 400.7). Timestamp orders continuous effects
 * within a layer (CR 613.7). PlayerId is a seat, stable for the whole game.
 * All three are plain integers; the aliases document intent.
 */

export type ObjectId = number;
export type PlayerId = number;
export type Timestamp = number;

/** "No object". Never allocated to a real object. */
export const NO_OBJECT: ObjectId = 0;
/** "No player". */
export const NO_PLAYER: PlayerId = -1;

/**
 * Monotonic id source, owned by one game. Plain data so a game clones by
 * copying it (the Python version was a slotted class cloned by pickle).
 */
export interface IdAllocator {
  nextObject: number;
  nextTimestamp: number;
}

export function newIdAllocator(): IdAllocator {
  return { nextObject: 1, nextTimestamp: 1 };
}

export function allocateObjectId(ids: IdAllocator): ObjectId {
  const id = ids.nextObject;
  ids.nextObject += 1;
  return id;
}

export function allocateTimestamp(ids: IdAllocator): Timestamp {
  const ts = ids.nextTimestamp;
  ids.nextTimestamp += 1;
  return ts;
}

/** The timestamp that would be handed out next, without consuming it. */
export function peekTimestamp(ids: IdAllocator): Timestamp {
  return ids.nextTimestamp;
}

export function cloneIdAllocator(ids: IdAllocator): IdAllocator {
  return { nextObject: ids.nextObject, nextTimestamp: ids.nextTimestamp };
}

// CR 115.4: objects and players share one "chosen targets" list. Object ids
// are positive, so a player is encoded as a negative number.

/** Encode a player as a target id. */
export function playerTarget(playerId: PlayerId): number {
  return -(playerId + 1);
}

export function isPlayerTarget(target: number): boolean {
  return target < 0;
}

/** Decode a player target id. */
export function targetPlayer(target: number): PlayerId {
  return -target - 1;
}
