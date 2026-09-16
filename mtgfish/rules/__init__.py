"""The rules engine: Magic's Comprehensive Rules, implemented.

Design rules for everything in this package:

1. **Effects are data, not closures.** An effect is an opcode plus typed
   parameters. It must be serializable and inspectable. This keeps the parser
   testable in isolation and keeps the hot path portable to a native module
   later.

2. **Continuous effects are derived, never mutated in place.** A permanent's
   characteristics are computed by running the layer system (CR 613) over its
   printed values plus the active continuous-effect set. Nothing writes a
   permanent's power directly.

3. **Deterministic ordering everywhere.** No iteration over unordered sets, no
   reliance on string hashing, no ambient randomness. Every choice draws from
   the game's seeded RNG in a fixed order, because replay reconstructs a game
   from its seed alone.

4. **Unknown is reported, never ignored.** An unrecognised keyword, symbol, or
   effect raises or is recorded as unimplemented. Silent no-ops are how a
   simulator lies to you.
"""
