/**
 * A small synchronous streaming hash for the game-log digest. Replaces
 * Python's hashlib.sha256 in mtgfish/rules/kernel/log.py: browser crypto is
 * asynchronous, and the digest only has to detect a game that does not replay
 * identically from its seed, not resist an attacker.
 *
 * Two independent 32-bit lanes (cyrb53-style mixing), finalised to 16 hex
 * digits. Text is fed as UTF-16 code units, which is deterministic.
 */

export interface Digest {
  h1: number;
  h2: number;
  length: number;
}

export function newDigest(): Digest {
  return { h1: 0xdeadbeef, h2: 0x41c6ce57, length: 0 };
}

export function cloneDigest(d: Digest): Digest {
  return { h1: d.h1, h2: d.h2, length: d.length };
}

export function updateDigest(d: Digest, text: string): void {
  let { h1, h2 } = d;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  d.h1 = h1 >>> 0;
  d.h2 = h2 >>> 0;
  d.length += text.length;
}

/** The hash of everything fed so far, as 16 hex digits. Does not consume. */
export function hexDigest(d: Digest): string {
  let h1 = d.h1 ^ d.length;
  let h2 = d.h2;
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const hex = (n: number): string => (n >>> 0).toString(16).padStart(8, "0");
  return hex(h2) + hex(h1);
}
