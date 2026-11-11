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
- `kernel/card.ts`: the printed card (FaceDef / CardDef from
  `mtgfish/data/cards.py`) in the IR's conventions (snake_case, numeric
  enums, IR ManaCost).
- `kernel/hooks.ts`: the seam for matching, values and conditions (see
  below).
- `rules/cr600_spells_and_abilities/layers.ts`: the layer system from
  `cr613_layers.py` with the front doors from `game.py`
  (`characteristicsOf(game, obj)`, `printedCharacteristics`, `board`, epoch
  caches), dependency ordering (CR 613.8), intrinsic land/Siege abilities,
  face-down 2/2s, bestow, keyword and P/T counters, CDAs, control.
  `test/rules/layers.test.ts` ports `tests/rules/test_layers.py` (Humility
  + Opalescence both orders, Blood Moon vs Urborg both orders, ...), run
  against a stand-in matcher (`test/rules/board.ts`) until rules/match/
  lands. KNOWN gaps, marked in the code: prototype faces (CR 718.2) and
  MTG Arena "perpetually" are not ported yet.
- `rules/emit.ts`: the event bus (`emit`): log and digest, the observer,
  this turn's tally, speed (CR 702.179d), Ring-bearers, and the CR 700
  terms in `rules/cr700_additional_rules/general.ts` (descend, expend,
  crime). Triggered abilities plug in with `installTriggerCollector` when
  that module is ported.
- `rules/zones.ts`: `moveObject` from `Game.move_object` (CR 400.7: new
  object, husk kept for last-known information; tokens cease to exist;
  finality, flashback-style exile, instants staying put, entering
  transformed, loyalty/defense on entry, battle protectors, commander
  identity), the zone-specific events, and `chooseProtector`.
  `rules/cr100_game_concepts/actions.ts`: `addCounters` through
  replacements. Replacement effects, the commander zone, self-entry
  replacements, the Attraction junkyard and Auras entering uncast plug in
  through `cr600_spells_and_abilities/replacement_hooks.ts` until cr614 and
  cr303 are ported (defaults change nothing). KNOWN, marked in the code:
  "perpetually" and the junkyard pile are not ported yet.
- `rules/ir_read.ts`: reads IR fields whether enums are strings or numbers,
  and `irEqual` for Python's dataclass equality of abilities.
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
- **`kernel/hooks.ts`:** layers and resolution call `matches`, `find`,
  `evaluate` and `holds` through `matchHooks()`. `rules/match/` installs
  its implementations with `installMatchHooks({...})` at import, so neither
  side imports the other. The signatures follow the Python functions with
  keyword arguments as an options object; change them there if they don't
  fit, and tell me.
- **Characteristics:** `characteristicsOf(game, obj)` in
  `rules/cr600_spells_and_abilities/layers.ts`. During a board computation
  the layers pass the in-progress characteristics as `override` to
  `matches`, as Python did; please honour it. `valueSubjects` is in the
  hooks too (Python `matching.value_subjects`).
- **Mana:** `rules/mana/` is yours now. I'd already ported
  `rules/cr100_game_concepts/mana_pool.ts` (ManaKind, ManaPool) and
  `mana_value.ts` (mana value of an IR mana cost); `Player.manaPool` uses
  the former. Move both into `rules/mana/` whenever you like and update the
  two imports (player.ts, characteristics.ts); I won't touch them again.

## For the parser session (match/)

- **This turn's events:** `game.turnHistory` is a `Map<string, number>`
  keyed by `turnHistoryKey(kind, player)` (total amount, at least 1 per
  event) and `turnHistoryCountKey(kind, player)` (number of events), both in
  `kernel/game.ts`. That is Python's `turn_history` with string keys, for
  EVENT_THIS_TURN conditions and event-count values.

## IR v2: ready on my side

Every IR read in rules/ and the tests now goes through `num`/`isEnum`
(`rules/ir_read.ts`), and every IR object the engine builds itself uses
`ir("Kind.MEMBER")`, which yields the number. Checked with 536557e applied
on a scratch branch: typecheck clean, all tests green; and green on v1 as
it stands. **Parser: re-land v2 whenever you like.**

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

1. Turn structure (CR 500-514) and the priority loop, then casting and
   activating (with the parser's mana code), then resolution.

## Questions for the auditor

- **Card data:** the engine needs printed card faces (name, mana cost, type
  line, P/T, colours) as well as the abilities in the IR. `kernel/card.ts`
  defines `CardDef`/`FaceDef` in the IR's conventions so the parser
  session's exporter can emit them from Python's `CardDef` 1:1 (either
  inside the card IR document or beside it). Who exports it, and where
  does the web session serve it from?

- None blocking. Please confirm the parser session agrees to numeric enums
  before I write code against them; until then I read enums through
  `enumValue`, which works for either form.
