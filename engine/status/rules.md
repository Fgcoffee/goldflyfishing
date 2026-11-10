# rules session: status

Update this file with every push: what landed, what's next, and any
**Questions for the auditor** (the auditor reads it, and every commit
message, at each check-in; direct messages to the auditor are refused).

## Done

All on `claude/modest-bohr-yqrqm7`, `npm test` and `npm run typecheck` green.

- `d7a1822` kernel foundations: `kernel/enums.ts` (generated from
  `kernel/enums.py`, values pinned by a snapshot test), `kernel/ids.ts`,
  `kernel/random.ts` (one sfc32 PRNG as plain data; `seedFor` matches
  Python's `RunConfig.seed_for` value for value), `kernel/digest.ts` +
  `kernel/log.ts` (synchronous streaming hash in place of sha256).
- `7c96b49` `kernel/gameobject.ts`; `rules/cr200_parts_of_a_card/typeline.ts`
  and `characteristics.ts` (holding IR `Ability` objects);
  `rules/cr100_game_concepts/mana_value.ts`.
- `fdf9421` `rules/cr100_game_concepts/player.ts` and `mana_pool.ts`
  (ManaKind keyed by a canonical string, since JS Maps compare by identity).
- `6d3d451` `kernel/game.ts`: game state, `cloneGame` for the lookahead
  (copies every mutable container, the PRNG and log digest included),
  APNAP order, zone lists, permanents, player control, `createObject`.
- `kernel/events.ts`: EventKind (generated; a test pins it to the IR's
  table), the event record, look-back-in-time kinds (CR 603.10).

## Interfaces for the parser session (mana/, match/)

These are published and stable enough to build on; tell me if a shape gets
in your way rather than working around it.

- `kernel/game.ts` `Game`: `objects: Map<ObjectId, GameObject>`, zone arrays
  (`battlefield`, `stack`, `exile`, `command`; owned zones on `Player`),
  `players`, `turn`, `activePlayer`, `phase`, `step`, `rng`, `epoch`.
  Queries: `player`, `permanents(game, controller?)`, `zoneList`,
  `objectsIn`, `apnapOrder`, `opponents`, `livingPlayers`.
- `kernel/gameobject.ts` `GameObject` (camelCase fields mirroring Python's;
  counters are a `Map<string, number>`), `isLive`, `isPermanent`, counters.
- `rules/cr200_parts_of_a_card/characteristics.ts` `Characteristics` and its
  query helpers (`hasType`, `isCreature`, `hasKeyword`, ...).
- **Coming next from me:** `characteristics(game, obj)` (the layer system),
  which `match/` will call. Until it lands, take a `charsOf(obj)` callback or
  call through a single function I'll fill in, so nothing has to change later.
- **Mana:** `rules/mana/` is yours now. I'd already ported
  `rules/cr100_game_concepts/mana_pool.ts` (ManaKind, ManaPool) and
  `mana_value.ts` (mana value of an IR mana cost); `Player.manaPool` uses
  the former. Move both into `rules/mana/` whenever you like and update the
  two imports (player.ts, characteristics.ts); I won't touch them again.

## Answer: the IR shape question

- **Enums: plain numbers, please,** not strings. The engine compares and
  masks them in its hottest paths (type checks, colours, event kinds,
  effect dispatch), and a flag like `"CardType.ARTIFACT|CREATURE"` would
  have to be decoded on every read or converted once into a second, numeric
  copy of every type. Numbers make the JSON the runtime form. The names stay
  in the schema and the generated `*Values` tables, and `ir_version` is
  bumped if Python ever renumbers a member (the kernel's enum snapshot test
  catches that on my side).
- **Field names: keep Python's snake_case.** It keeps the JSON a 1:1 image
  of the Python classes the auditor diffs against, and the generated types
  stay mechanical. The engine's own state (Game, GameObject, Player) is
  camelCase; IR objects are read as they come.

## Next

1. The event bus (`emit`, observers, per-turn records) with the layer work.
2. Characteristics and the layer system (`cr613_layers.py`) on the IR, with
   `characteristics(game, obj)` and its epoch cache.
3. Moving objects between zones (`move_object`), with replacements and
   triggers as they're ported.

## Questions for the auditor

- None blocking. Please confirm the parser session agrees to numeric enums
  before I write code against them; until then I read enums through
  `enumValue`, which works for either form.
