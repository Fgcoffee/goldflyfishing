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
- Turn structure and priority: `rules/cr500_turn_structure/turn.ts`
  (`runGame`, `takeTurn`; cr500_turn.py) and
  `rules/cr100_game_concepts/priority.ts` (`runPriority`, `settle`, the
  `Agent` interface, `PassingAgent`, `Action`; cr117_priority.py). Also
  relaxations, durations (cr611), draw/discard/mill/shuffle, losing and
  winning, Saga lore counters.
- **Placeholders**, at their final paths and marked `PORT:` in the code,
  so imports won't change when each is ported: `legality.ts` (only passing
  is legal), `loops.ts`, `cr600_spells_and_abilities/{casting,stack,
  triggers}.ts`, `cr700_additional_rules/{sba,designations,attractions}.ts`,
  `cr500_turn_structure/combat.ts`, special actions, and
  CR 727 restarts. Games run end to end with passing agents.
- `rules/ir_read.ts`: reads IR fields whether enums are strings or numbers,
  and `irEqual` for Python's dataclass equality of abilities.
- **Casting, in progress** (cr601_casting.py, split by topic):
  `restrictions.ts` (prohibitions and permissions, CR 101.2, 113.6; the
  blocking requirements come with combat), `targeting.ts` (CR 601.2c, 115),
  `modes.ts` (CR 700.2), `cr100_game_concepts/protection.ts` (protection,
  hexproof, shroud for objects and players), `effect_props.ts`,
  `cr100_game_concepts/costs.ts` (cr118_costs: components, TotalCost),
  `total_cost.ts` (CR 601.2f with alternative, additional, spree, emerge,
  commander tax, cost changes), `cost_checks.ts` (the payability dry run and
  what can pay each component), `paying.ts` (paying spells with convoke,
  improvise and delve; activation costs and limits; player-scoped costs),
  `mana_host.ts`, and from resolution the dispatcher with ADD_MANA and
  `resolveManaAbility`; `casting.ts` itself: `castSpell` (CR 601.2 with the
  rewind), `playLand`, `activateAbility`, `pushAbility` (stack.ts); and
  `legality.ts` (`legalActions`, `hasSorcerySpeed`, `canAfford`) with
  `rule_overrides.ts` (CR 101.1), `special_actions.ts` (CR 116),
  `cr400_zones/outside_game.ts` (companion) and the part of cr708 special
  actions needs (`face_down.ts`: turning face up). Casting is done, replicate/conspire copy triggers included (0be4a86).
  The CR 718.2 prototype face itself is still KNOWN in layers.
- **Resolution, in progress:** `stack.ts` ports cr608_stack.py in full
  (resolveTop, fizzling on all-illegal targets, permanent spells entering
  with links, counters, Auras and paid-cost riders, instants and sorceries
  leaving to the graveyard, exile, library or hand). `resolve.ts` has the
  dispatcher with the "it" bookkeeping, object selection (`objectsOf`:
  targets, "it", piles, counts, per-player things), and executors for
  SEQUENCE, CONDITIONAL, REPEAT, OPTIONAL, UNLESS_PAYS, NOTHING,
  EXTRA_TRIGGER, ADD_MANA, DRAW, MILL, DESTROY, SACRIFICE, RETURN_TO_HAND,
  MOVE_ZONE, DISCARD, CREATE_TOKEN (token copies included), RESTRICTION,
  SUSPEND_RULE, ROLL_DICE, DAMAGE, GAIN_LIFE, LOSE_LIFE, SET_LIFE,
  ADD_POISON, DESIGNATE, FIGHT, TAP, UNTAP, ADD_COUNTERS, DOUBLE_COUNTERS,
  REMOVE_COUNTERS, PROLIFERATE, ATTACH, UNATTACH, GAIN_CONTROL, TRANSFORM,
  COPY_PERMANENT, COPY_SPELL, GOAD, COUNTER_SPELL, and continuous effects
  from resolving spells (`registerContinuous`, CR 611.2: settled sets,
  locked amounts, player abilities) as the default for effects.py's
  CONTINUOUS_KINDS; PLAYER_WINS, PLAYER_LOSES, BECOME_MONARCH, IF_YOU_DONT,
  PAY_COST, START_ENGINES, ADD_ENERGY, EXTRA_LAND_DROP, EXTRA_TURN,
  SEARCH_LIBRARY, SHUFFLE, REVEAL, SCRY, SURVEIL, PUT_ONTO_BATTLEFIELD,
  PUT_ON_LIBRARY, LOOK_AT_TOP, EXPLORE, SEEK, CONJURE and PERPETUALLY
  (through your digital_mechanics.ts; zones.ts carries perpetual changes
  and layers.ts applies them, as you asked), REPLACEMENT, PREVENT_DAMAGE,
  REDIRECT_DAMAGE and ENTERS_AS_CHOICE (through your replacement.ts:
  `register`, `replacementFromEffect`, `becomeChosenCharacteristics`).
  Also TURN_FACE_UP, PHASE_OUT, REGENERATE, MONSTROSITY, ADAPT,
  EXCHANGE_CONTROL, EXCHANGE_LIFE, END_TURN, SKIP_STEP, ADD_EXPERIENCE,
  HARNESS, PAIR, SEPARATE_INTO_PILES, BECOME_SADDLED, BECOME_RENOWNED, HAUNT.
  Also VILLAINOUS_CHOICE, CHOOSE_MODE, SET_CLASS_LEVEL, BECOME_SOLVED,
  FLIP_PERMANENT, CHANGE_TARGETS, CAST_WITHOUT_PAYING (copies too),
  CASCADE, DISCOVER, PLAY_FROM_ZONE, EXTRA_PHASE, EXTRA_STEP,
  UNTAP_STEP_SKIP, VOTE, REPEAT_PER_PAYMENT, GIVE_GIFT.
  Also CONTROL_PLAYER, RESTART_GAME, BECOME_PREPARED.
  Also CHOOSE_QUALITY and TAKE_INITIATIVE (your designations.ts), and
  EXILE (with the CR 610.3 "until" return), DELAYED_TRIGGER and
  REFLEXIVE_TRIGGER (3582b6f); `createDelayedTrigger` is exported from
  resolve.ts. BLOCKS_SOURCE_IF_ABLE is done too (1ed92e2). Wired to your modules: TURN_FACE_DOWN and MANIFEST (face_down.ts); VENTURE is wired to your dungeons.ts; RING_TEMPTS and
  SACRIFICE_BLOCKERS_AT_END_OF_COMBAT to your ring.ts (combat.ts now imports
  your `isARingBearer`); OPEN_ATTRACTION and ROLL_TO_VISIT to your
  attractions.ts. **Every executor in resolve.py is now registered.** actions.ts has the damage and
  life rules (dealDamage with wither, infect, toxic, commander damage and
  lifelink; gainLife, loseLife, replacedLifeLoss, addPoison, excessDamage).
  The rest follow in resolve.py's order. A smoke test plays
  lands and casts creatures end to end and replays to the same digest.
- **Combat, done (b2f823b):** `combat.ts` ports cr506_combat.py,
  `combat_costs.ts` ports combat_costs.py (`taxRefusal` included), and
  restrictions.ts has the requirements half (`Requirement`,
  `requirements`, `blockObligations`, `enforceBlockRequirements` with the
  CR 509.1c search, `registerStandingRequirement`). `Combat` is plain data
  (Maps and Sets) matching match/'s `CombatView`, plus `controllers` and
  `leftAttacking`. Agents may implement `declareAttackers`,
  `declareBlockers`, `assignCombatDamage` (a Map keyed by recipient
  index) and `chooseEnlist` (`CombatAgent`); without them nobody attacks
  or blocks and the default division is lethal-first. Commander damage
  passes `isCommanderSource`, as the auditor asked. `removeFromCombat`
  (actions.ts) is now Python's full version.
- **`play(setup, index, seed)`, done (50fcbfb):** `rules/play.ts`.
  `rulesEngine` is the `GameEngine`; `play` and `playGame` (which also
  returns the finished game) take options `{ agentFor, log, watch }`. It
  builds decks and an ability provider from the RunSetup's IR once per run
  (cached by setup object), imports `rules/match` and `replacement` once,
  sets the card catalog (the run's own cards, by name), seats agents and
  returns a `GameRecord`. As in Python's `play_one`, no mulligans are taken.
  The record half of sim/observer.py (`Observer`, `finish`) is ported in
  play.ts. Tested on the four bench decks in schema/samples: games run to
  the cap or a win, a seed always gives the same digest. `runGame` now
  restarts games (CR 727, 4a7e76a).
- **Build fingerprint:** already in place from the web session: a content
  hash of engine/src (webapp/engine-build.mjs = mtgfish/web/engine_build.py),
  stamped at build by Vite and stored with every uploaded run, so every
  game record of a run carries it through its run. It replaces "version
  plus commit" (it changes exactly when the engine source does). `play`
  runs unchanged in Node: play.test.ts plays the bench decks in Vitest's
  Node environment, and nothing in engine/src touches a browser global.
- emit.ts calls `collectTriggers` at emit time (ce40f0e): combat's imports
  made the load-time install run before emit existed.
- **Triggers, done (19e2a40):** `triggers.ts` ports cr603_triggers.py in
  full. Published: `PendingTrigger` with `pendingTrigger(source, ability,
  event, controller, batch?)` (your designations.ts `SourcelessTrigger` is
  compatible: a missing `batch` never folds), `DelayedTrigger` with
  `delayedTrigger(...)`, `collectTriggers` (installed on the event bus
  when triggers.ts loads), `putTriggersOnStack`, `checkStateTriggers`,
  `checkInterveningIf`, `subjectOf`, `conditionMet`,
  `isTriggeredManaAbility`, `resolveManaTriggers`. Look-back covers departed
  tokens, cards and whole batches (CR 603.10a). Game gained
  `armedStateTriggers` and the watcher index (`triggerIndex`,
  `triggerIndexKey`; dropped by `cloneGame`).
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

- `resolvePlayers` and `defendingPlayers` hooks: installed (869a1b1), thanks.
- **One more hook, please:** `wasUndeterminable()`, your `values.ts` function of the
  same name. Costs read it right after `evaluate` (CR 903.4f: an
  undeterminable amount makes a cost unpayable). Until installed it says
  `false`, so such a cost counts as zero rather than unpayable.

- **This turn's events:** `game.turnHistory` is a `Map<string, number>`
  keyed by `turnHistoryKey(kind, player)` (total amount, at least 1 per
  event) and `turnHistoryCountKey(kind, player)` (number of events), both in
  `kernel/game.ts`. That is Python's `turn_history` with string keys, for
  EVENT_THIS_TURN conditions and event-count values.

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

## For the parser session: the mana host is done

`ManaPlanHost<Game>` is implemented in
`rules/cr600_spells_and_abilities/mana_host.ts` as `MANA_HOST`, with the
game-side wrappers Python had: `planPaymentFor(game, player, cost,
context)` (plan_payment, including its early return when the pool already
pays), `canProduceFor` (can_produce) and `executePlanFor` (execute_plan; it
turns `PlanNoLongerHolds` into a CastError, as Python's F61 guard raises).
`manaOptions`, `stillActivatable` (by `irEqual`) and the `_predict` port are
there too. Your Mana planning roadmap item can be ticked.

- **Correction on tapping:** Python's `_pay_activation` sets `tapped` and
  emits TAPPED itself rather than calling `actions.tap`, and the port does
  the same. `tap` / `untap` in actions.ts are for effects.
- **Payment context:** `paymentContextOf(obj)` gives a restriction the
  printed face's types and subtypes, `{}` for a token, `null` for a bare
  check, as `SpendOnlyOn.permits` reads them.

## For the parser session: SBAs, setup, copies/faces/tokens (moved to you, 084182b)

**State-based actions plug in at one function.** Implement
`checkStateBasedActions(game): boolean` in
`rules/cr700_additional_rules/sba.ts` (the placeholder is there; replace it
in place, it is yours now). It returns whether any SBA was performed. My
code calls it in two places, and both are already wired:
- `settle(game)` in `rules/cr100_game_concepts/priority.ts`: before each
  priority, SBAs, then state triggers, then putting triggers on the stack,
  repeated until nothing happens (CR 117.5, 704.3). Returns early on
  `game.gameOver`.
- cleanup in `rules/cr500_turn_structure/turn.ts` (CR 514.3a): an SBA in
  cleanup gives priority and repeats the step.
What it can call from me: `actions.ts` has `destroy` (with indestructible,
"can't be destroyed", regeneration), `isIndestructible`, `sacrifice`,
`putIntoGraveyard`, `detach`, `exile`, `removeFromCombat`, `playerLoses`,
`checkGameOver`; `zones.ts` has `moveObject`; `protection.ts` has
`protectedFrom` / `playerProtectedFrom` (CR 702.16c-d for Auras);
`targeting.ts` has `enchantableScope`. Combat is still a placeholder
(`combat.ts`, attackers only); `check_removal_from_combat` comes with my
combat port, so leave a call to it marked `PORT:` if you need one.
Commander damage and the legend rule's choice go through the agent the
same way Python does (`agentFor`).

**Copies, faces and tokens.** Resolution (mine) calls into your modules at
these points; please export them with these names:
- `createTokens(game, spec, controller, count, { source })` and
  `createEmblem(...)` from `rules/cr100_game_concepts/tokens.ts` (Python
  `cr111_tokens.create_tokens`, `create_emblem`), called by my CREATE_TOKEN
  executor;
- from `rules/cr700_additional_rules/faces.ts`: `transform(game, obj)`,
  `copyPermanent(...)`, `copySpell(...)`, `chooseNewTargets(game, copy,
  controller)` and `MAY_CHOOSE_NEW_TARGETS`, called by TRANSFORM,
  COPY_PERMANENT and COPY_SPELL. Same signatures as Python's
  `cr707_faces.py`, keyword arguments as an options object.
- **Your PENDING(rules) records in `match/conditions.ts`, answered:**
  - FIRST_RESOLUTION_OF_NAME: `game.spellResolutions` exists now, a
    `Map<string, number>` keyed `` `${controller}|${name.toLowerCase()}` ``
    (Python's `(controller, name)`), written by stack.ts after a spell resolves.
  - WAS_KICKED with a keyword: `paidAdditionalKeywords(game, obj)` in
    `rules/cr700_additional_rules/paid_costs.ts`.
  - COMBAT_DAMAGE_DEALT_BY and COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE:
    `combatDamageDealtBy(game, condition, controller)` and
    `combatDamageBySharedType(game, sourceId, controller)` in
    `rules/cr700_additional_rules/combat_damage_record.ts`; `dealDamage`
    records the source on combat damage to a player.
- Token copies use your `copyTokenCard` now; my private copy is gone.
- **Legend rule (CR 101.1):** `ruleIsSuspended(game, Rule.LEGEND_RULE, { obj })`
  from `rules/cr100_game_concepts/rule_overrides.ts`; it returns the
  override or null, as `game.rule_is_suspended` does. Please call it in
  `checkLegendRule`.
- **Face-down (cr708):** I ported only what special actions needs, in
  `rules/cr700_additional_rules/face_down.ts` (`manaCostToTurnUp`,
  `cannotTurnFaceUp`, `turnFaceUp`, `reveal`). If cr708 counts as faces for
  you, the rest (manifest, cloak, turning face down) can go in that file.
- `faces.ts` exists already: I wrote `layoutOf`, `castableFaceIndices` and
  `playableLandFaceIndices` because legal actions needs them now. The file
  is yours from here; keep those three.
- Already in my `layers.ts`, so not needed again: `copiableCharacteristics`,
  `faceDownCharacteristics`, `fromFace`. The CR 718.2 prototype face is
  still marked KNOWN there; `hasPrototypeLine` is in `card_types.ts`, and
  you're welcome to build the face in layers' `printedCharacteristics`.
- Cast modes (`CastMode`, `castModeForFace`, `RESOLUTION_ZONE`) are in
  `cr300_card_types/card_types.ts`; `zones.ts` keeps a spell's mode on the
  permanent (`PERSISTS_ON_BATTLEFIELD`).
- Setup (CR 103): `newGame` / `createObject` in `kernel/game.ts`,
  `shuffleLibrary` and `draw` in actions.ts, `runGame` in turn.ts.
  `play(setup, index, seed)` stays mine and will call your setup.

## Next

Aimed at `play()`, the gate for the first full game (milestone 4):
1. Resolution executors: all done.
2. Triggers: done.
3. Combat: done.
4. `play(setup, index, seed)`: done; the web session can swap it in.
5. The build fingerprint and the board tests: done (two cards still to
   come, below).
6. Then, from the auditor (9603cce), the web session's items: per-turn
   series and removal in the game records (done: records 78cde2a, report
   d2def4b), and the server-side spot check (done, below) (a Node build with a replay CLI, Node in the
   Dockerfile, a background job in mtgfish/web/browser_runs.py).
(Moved to the parser: SBAs, setup, copies/faces/tokens 084182b;
replacements and prevention, commander rules, the in-game CR 700
mechanics 18f9e4f.)

## Board tests (the last rules item, in progress)

757 Vitest cases green. Ported, whole or in part (the files' headers name
their Python sources): test_actions, test_block_requirements, test_combat,
test_combat_removal, test_cr101_rule_overrides, test_cr115_targets,
test_cr116_special_actions, test_cr118_costs, test_cr119_life,
test_cr120_damage, test_cr508_combat_costs, test_cr510_damage_division,
test_cr510_damage_to_a_player, test_cr601_casting, test_cr602_activation,
test_cr603_cast_this_spell, test_cr603_delayed_remembered,
test_cr603_simultaneous_deaths, test_cr603_trigger_correctness,
test_cr603_trigger_events, test_cr608_remembered, test_cr611_continuous,
test_cr107_numbers, test_cr110_permanents, test_cr112_spells,
test_cr119_pay_all_life, test_cr120_excess_damage,
test_cr122_player_counters, test_cr406_exile, test_cr500_phases,
test_cr607_linked_abilities, test_cr610_exile_until, test_casting (on your
real-card fixtures, casting_real.test.ts), test_cr603_whenever_events, test_cr608_untargeted_count, test_cr613_effects_outside_the_board (not the
Airbend cases: Python's cr701_keyword_actions.build has no TS counterpart),
test_cr613_granted_timestamps, test_cr500_phase_triggers,
test_cr603_if_you_cast_it,
test_cr700_modes, test_cr701_cascade, test_cr701_search, test_cr706_dice,
test_layers, test_legality, test_resolve; play.test.ts plays the bench
decks end to end.

Many Python tests read their cards with the parser or the Python card
database, which TS doesn't have; their IR is written by hand, so they are
ported case by case rather than file by file. Also ported:
test_cr116_foretell_cost; on your first fixture batch (f6adad6):
test_cr118_force_of_will, test_cr115_target_identity (the engine cases),
test_cr107_x_in_activation_costs, the rest of test_casting and
test_cr603_cast_this_spell; on the second (6441531): Chandra and Helix
Pinnacle's X, test_cr613_keyword_removal, test_cr603_granted_triggers,
test_cr601_additional_cost_record, test_cr601_target_counts,
test_cr608_pay_if_you_do (and Rest in Peace), test_cr118_alternative_costs,
test_cr601_granted_cast_abilities. 809 engine tests green.
**The item is ticked.** Still open, all small:
- (Done: each_opponents_cards_real.test.ts and target_counts_real's Ulamog,
  the Ceaseless Hunger case, on the parser's d59284f fixtures.)
- test_cr608_each_opponents_cards uses Soul-Guide Lantern, which I didn't
  ask for (I named Rest in Peace by mistake; it went to another file).
- test_cr601_target_counts's Ulamog case is Ulamog, the Ceaseless Hunger;
  I asked for the Infinite Gyre by mistake, so that case is adapted.
- test_cr603_granted_triggers's persist and modular cases build the
  granted keyword with Python's cr702_keyword_impl, which has no TS
  counterpart; they stay in Python unless a fixture grants either.
(test_cr108_* and test_cr605_modal_mana are on the parser's list.)

## Server-side spot check (done)

- **Engine** (2cb63b4): `sim/engines.ts` has `ENGINES` (stub, rules) and
  `DEFAULT_ENGINE`, inside engine/src so the build fingerprint covers which
  engine the page plays. `sim/replay.ts` `replayGames(request)` plays games
  and returns digests; `cli/replay.ts` wraps it for Node, bundled by
  `npm run build:replay` to engine/dist/replay.cjs (esbuild 0.21.5 pinned).
- **Server** (8ab95a8): `browser_runs.check_run` rebuilds a run's setup
  from the exported IR (each card's hash must still match the run's),
  replays a deterministic sample of 8 games in Node and marks the run
  `ok`, `mismatch` or `unverifiable` (a card changed since, or another
  engine build), with `spot_check_detail`. A daemon thread in app.py runs
  `check_pending` every `MTGFISH_SPOT_CHECK_INTERVAL` seconds (300; 0 off;
  first pass after one interval); without node or the CLI it logs and stays
  off. tests/web/test_spot_check.py: 7 tests, one replaying the bench decks
  through the real CLI. tests/web: 94 passed, 39 skipped (no card database
  in this container).
- **Dockerfile:** a `node:22-slim` stage runs `npm ci` and
  `npm run build:replay`; the final image gets the node binary,
  libstdc++6 and engine/dist. The stage's steps pass from a clean copy, but
  I couldn't build the image: no Docker daemon here. The next deploy is the
  first real build.
- The page plays `DEFAULT_ENGINE = "rules"` now (parser, a41d93e), and the
  replay reads the same constant. Rechecked after the AI port (ad1d7cf):
  775 engine tests green, the CLI bundles, and test_spot_check.py's real
  replay passes. With SimpleAgent a full game is about 15 s, so a run's
  8-game sample is about 2 minutes, inside `REPLAY_TIMEOUT` (300 s).

## For the web session: the real engine is ready to swap in

- `import { rulesEngine } from "engine/src/rules/play"` replaces
  `stubEngine`; same `GameEngine` shape, no other change.
- **Agents:** Python's sim seats `SimpleAgent` (mtgfish/ai/simple.py). That
  is your AI item; until it lands `rulesEngine` seats `PassingAgent`, so
  every game is a stall-out. `play(setup, index, seed, { agentFor })` takes
  any agent factory: the test in test/rules/play.test.ts uses a small eager
  one (plays lands, casts, attacks).
- **WinReason:** records.ts's union (`LIFE`, `DECKING`, `CONCEDE`, `DRAW`,
  ...) doesn't match Python's `WinReason` (`COMBAT_DAMAGE`,
  `NONCOMBAT_DAMAGE`, `MILL`, `LAST_STANDING`, `LOOP_DRAW`, `LOOP_KILL`,
  `CONCEDED`, ...). play.ts maps Python's onto yours (the three damage
  kinds -> `LIFE`, `LAST_STANDING` -> `ALTERNATE`, `LOOP_DRAW` -> `DRAW`).
  If you switch records.ts to Python's names, tell me and I'll record the
  exact reason (the observer already tracks the last combat-damage turn).
- The record's `cast[].round` is the hero's own turn number, as your
  records.ts documents.

## For the parser session

- `wasUndeterminable`: installed by you in 0d33632, thanks; numbers.test.ts's CR 903.4f case is an ordinary test now.
- **Fixtures:** the second batch is in (6441531, thanks) and ported. Two
  more, if you would: Soul-Guide Lantern and Ulamog, the Ceaseless Hunger
  (my mistakes above). And `cardCatalog(game)` in turn.ts (9b48947): thanks.

- turn.ts's CR 727 restart reads the card catalog off the game by cast
  (`cardCatalog`), as digital_mechanics.ts stores it. A `cardCatalog(game)`
  getter there would be tidier; say if you add one.

## Resumed 28 Nov: "that player" in object filters (17bf183)

- Matching takes `thatPlayer`: a filter whose owner or controller is
  SPECIFIC with no player named binds to it (unset: matches nothing, as
  before). Values pass it to counts. resolve.ts `boundPlayer`: the player
  an executor is carrying the instruction out for, else the one player
  chosen earlier, else the trigger event's player. Damage, life gain and
  loss, and per-player sacrifice evaluate per player when their filter
  reads it (`readsThatPlayer`; otherwise unchanged). Trigger targets are
  chosen and checked with the event's player.
- Tests (real cards): All Is Dust, Stronghold Discipline, Acidic Soil,
  Ragavan (exiles the damaged player's top card), Throat Slitter (targets
  the damaged player's creature, and doesn't fizzle).
- **For the parser:** Ragavan's exile works now (your 864afae). Primeval
  Light and How to Start a Riot read with no "target player" (no player
  in the IR, nothing to bind): the reading needs the player target.
  Living Death's sacrifice lost "each player". Cards in the list whose
  "that player" is another target's controller (Public Execution's "each
  other creature that player controls") would need the targeted object's
  controller: not done yet; say which shapes matter most.
- Next (auditor's order): the dummy-ability backlog, then the incremental
  board.

## PAUSED (15 Nov, 19:20 UTC, at the user's request via the auditor)

Stopped before writing any incremental-board code; nothing half-done.
The approved plan (with the auditor's conditions) is the proposal below.
Where it stood, for resuming:
- Design settled: a dirty-object journal next to the epoch
  (`invalidateObjects(game, ids)` typed; `invalidateAllCharacteristics`
  stays global); a partial recompute that runs the existing layer code
  on scope = dirty objects + every object with static abilities, and keeps
  the cached characteristics for the rest; preconditions checked each
  time (no expiry or removal of effects since the cache, every effect's
  amounts constant, no static conditions, filters reading only the object
  itself, `noDependencyPossible` in every layer, no control, CDA or
  perpetual effects); a mode switch off / check (compute both, throw on
  any difference) / on, off by default.
- First typed call sites: `createTokens` (the new token),
  `registerContinuous` for effects with a `specific` target list (those
  targets), and resolveTop's catch-all when the resolving ability's
  effect tree is only MODIFY_PT, GRANT_ABILITY, CREATE_TOKEN, SEQUENCE.
- Then: check mode on the 200 bench games, the user's 1,000 and the Vitest
  suite; turn it on; measure game 9; raise the cap to 5,000.

## Wide boards: what's left, and a proposal (for the auditor)

- Measured and dropped (no commit): starting the lookahead's clones from
  the original's board, trigger-index and restriction caches. Exact
  (identical digests on 26 games) but no gain (52.1 s vs 53.1 s), so the
  clones' first recompute is not the bottleneck.
- Where game 9's remaining time goes: about three full board recomputes
  per token (its own epoch bump, each Ogre Battledriver trigger's
  resolution, the catch-all bump after every resolution), each O(board).
  Every exact shortcut within one recompute is done (0454f23, 7e1d3ba).
- **Proposal: an incremental board, exact by construction and checked.**
  1. Typed invalidation: `invalidate(game, { objects?, global? })` in place
     of the blanket epoch bump, call site by call site (68 sites plus the
     catch-all in resolveTop). Anything not yet typed stays global, so
     each step is safe on its own.
  2. A partial recompute when the only changes since the cached board are
     to listed objects (a token entering, counters, tapping) and the
     effect set is "closed": every effect's amount constant, no filter
     reading other objects or counts, and `noDependencyPossible` in every
     layer. Then only the listed objects are recomputed; otherwise a full
     recompute, as now.
  3. A check mode (tests and the bench): compute both, assert equal. Run
     on the 200 bench games and the user's 1,000 before turning it on.
  Effort: a day or two; risk medium, contained by (3). Expected gain on
  swarms: most of the per-token recomputes become O(1). Median games gain
  little (their time is the bot's decisions and payment planning).
  **Shall I go ahead?** Until then the 500 cap stays, and the parser's AI
  fix (attacking with tokens) is what keeps swarms small.

## For the parser (answers, 15 Nov)

- **F37 engine half: fixed** (85b2113). `componentAmount` takes the
  paying object as its source; for a spell (context.ability === null) X
  is the spell's own, so Toxic Deluge cast with X = 3 pays 3 life as it's
  cast. Your "cast with X = 3" test is a plain `it` now. An ability's X
  isn't on its source permanent, so abilities are evaluated as before.
  Legality offering X: neither engine picks an X above 0 for a spell (the
  bot casts every X spell at 0, in Python too); the action brings the X.
  Choosing X is the AI's (yours) if you want it.
- **F3 `resolvePlayers` in EvaluateOptions: yes, suits me.** Add the
  option in values.ts and wire it in resolve.ts's `evaluateContext` from
  `playersOf` / `referredPlayers` yourself; a one-line change in my file is
  fine, no need to wait for me.
- **Reviewed fb9edb1's engine fixes:** both right.
  `commanderZoneReplacement` skipping the CR 903.9b choice for a commander
  already in the command zone is correct (nobody moving it out would choose
  "instead"). `restrictionPermits`' commander check is right for the usual
  case; note `commander` is a boolean, so "your commander" doesn't check
  ownership (casting an opponent's commander with Jeweled Lotus mana would
  pass). Rare; log it if you like.

## The user's decks: 1,000 games in Node (15 Nov)

Fixture `engine/test/fixtures/user_run_15nov/` (Vraska x2, Muddle, Kilo;
runSeed 0, maxRounds 25), engine at a4f3ccf, `playGame` with SimpleAgent,
60 s watchdog per game:
- **0 crashes, 0 timeouts, 0 runaways** (no TOKEN SWARM, no WORK BUDGET,
  no action budget); every game decided: 954 LIFE, 37 DECKING, 9
  COMMANDER_DAMAGE, 0 stalls.
- Times: median 1.37 s, p99 6.7 s, **max 8.6 s**; nothing over 10 s, so
  nothing to profile. Work: median 59,000 units, max 456,000 (under a
  quarter of the budget). Widest board: 251 permanents.
- The games that hung before F65 (169, 190, 271, 284, 307, 360, 386, 424,
  435, 497, 581) and the web session's 16 (157, 182, 283, 297, 334, 431,
  470, 537, 560, 581, 598, 627, 715, 850, 883, 941) all finish, in 0.6-2.8 s.
- Loops found in 54 games: mostly the bot re-equipping the same equipment
  ("stopped": Lost Jitte 20, Skullclamp 14, Paradise Mantle 14, Wizard's
  Staff 13, Hawkeye's Bow 11, The Reality Chip 3), Pemmin's Aura untaps (5,
  stopped), and real shortcuts: Aura of Dominion's tap-proliferate (3) and
  Sensei's Divining Top with a cast-token trigger (1).

## Loop detection ported; no game runs for minutes (15 Nov)

- **loops.ts** is a port of `mtgfish/rules/kernel/loops.py` (687ef75):
  optional loops are shortcut to their result (CR 732.2a: far enough to
  kill every opponent they can reach, never their controller, never past
  their fuel; re-aimed one opponent at a time), an unstoppable mandatory
  loop is a draw (CR 104.4b), a loop that changes nothing is stopped and
  recorded. `game.loops` lists them; SimpleAgent's activation cap spares a
  loop being re-aimed (`isContinuing`). Tests: `test/rules/loops.test.ts`,
  every case of `tests/rules/test_loops.py` (Sanguine Bond + Exquisite
  Blood to exactly 81 life, and 161 at a four-player table; Zulaport
  Cutthroat under a 30-token wipe is not a loop; the synthetic cycles and
  `decide`'s arithmetic), except the WinReason.LOOP_KILL one (the TS record
  maps loop kills to LIFE, as it maps every life loss). No Python bug found.
  Where Python iterated a set union of keys (arbitrary order), TS uses a
  fixed order.
- **Its effect on the 200 bench games** (674b258 vs 687ef75): outcomes
  identical (163 LIFE, 22 COMMANDER_DAMAGE, 11 TOKEN SWARM, 2 STALL_OUT, 2
  DECKING). 14 digests change because a loop was found: 10 Sanguine Bond +
  Exquisite Blood games shortcut (same winner), 4 Lightning Greaves
  re-equip loops stopped as "changes nothing". The 11 swarm games' digests
  change only by the reworded runaway log line.
- **Work budget** (687ef75, 4d77859): every board computation adds its
  object count to `game.work.units`, shared with the bot's lookahead
  clones; past `WORK_BUDGET` (2,000,000) the priority loop stops the game
  as a runaway ("WORK BUDGET <top of stack>"). Deterministic, so replayable.
  Bench: finished games use median 42,000 and at most 600,000 units (about
  22 us each here); game 9 used 1.35 million.
- **F65 follow-up (auditor's (b)):** paying.ts:533, triggers.ts:195,
  resolve.ts:2235, stack.ts:129 and values.ts:211 read `supersededBy` one
  step and then check `isLive`, which a self-linked husk never is: no loop,
  no change needed.
- **For the auditor:** I can't run the web session's 16 hanging seeds: the
  user's decks (Archidekt 27600141, 26926766, 26074514) aren't in the repo.
  If you commit their RunSetup JSON (e.g. under `engine/test/fixtures/`), I'll
  run them in Node.

## Speed on wide boards (auditor's top priority, 15 Nov)

Three exact optimizations (0454f23, 7e1d3ba, 5df22d0). **All 200 bench
games give identical digests** before (1d6b8df) and after (5df22d0).
- Finished games: median 1.15 s -> 0.98 s (-15%); total 304 s -> 243 s.
- All 200 games: 478 s -> 309 s (-35%).
- Swarm games (stopped at the 500 cap): game 9 79 s -> 19 s; 70 19 s ->
  6.9 s; 107 13.7 s -> 5.4 s; 184 10.4 s -> 2.8 s; 187 12.0 s -> 4.9 s.
- What changed:
  - layers: an effect is applied only to the objects its filter names; the
    CR 613.8 dependency search pairs only effects that can meet, walks the
    smaller side, skips objects where B changed nothing A's filter reads,
    and is skipped outright when every effect in a layer only changes P/T
    or only adds abilities and no filter there reads those fields;
    `dependsOn`'s "what it does" comparison was dead (it reads the game,
    never the characteristics handed to it) and is gone;
  - casting: each permanent's cost-changing and stack-granting statics are
    read once per epoch, not once per card in hand per listing; granted
    keywords are one object per word, so grants compare by reference.
- **Not reached: game 9 under 5 s.** What's left is the number of full
  board recomputes: about three per token (the token's own epoch bump,
  each Ogre Battledriver trigger's two effects, the catch-all bump after
  every resolution), each O(board). An incremental board needs every
  mutation to say what it changed (68 invalidation sites plus the
  catch-all in resolveTop, which tapping, counters and other live fields
  rely on). That's a redesign; I'd rather agree the plan first.
- **Median-game hotspots now:** the bot's `decide` is ~48% (legalActions
  27%, of which payment planning in `affordable` ~9%; the lookahead
  `project` 21%), board recomputes ~33%, trigger collection ~14%.
- **The cap stays at 500 for now.** Swarm cost grows with the square of
  the board, so a game run to 5,000 permanents would take many minutes at
  today's speed. Raise it once the parser's AI fix (attacking with
  tokens) keeps swarms small; then I'll rerun the bench and report how
  often the cap is hit.

## Token swarms: the cap (1d6b8df) and the bench after it

**200-game bench** (same decks, runSeed 20261114, maxRounds 25, Node,
SimpleAgent, 90 s watchdog), engine at 1d6b8df:
- **0 crashes** (F63 and game 63's hang are fixed on the parser's side).
- Endings: 129 LIFE, 47 COMMANDER_DAMAGE, 5 DECKING, 5 STALL_OUT (the
  100-turn cap), **14 TOKEN SWARM runaways (7%)**, all Krenko, Mob Boss:
  games 9, 27, 33, 40, 47, 70, 77, 102, 107, 117, 155, 173, 184, 187. No
  game hit the watchdog.
- Finished games: median 1.15 s, p90 3.0 s, slowest 11.6 s. The widest
  finished game peaked at 422 permanents (p99 388, median 55), so the cap
  of 500 stops only the swarms.
- Game 9 (seed 1673232466) still takes 79 s to reach the cap: Ogre
  Battledriver's continuous effects make each step costly on a wide board.
  Games 70 (19 s) and 107 (14 s) are the next slowest.
- **Runaways are their own line** (this commit): the record's win reason
  is `RUNAWAY` (new; Python had none), and `buildReport` counts them in
  `runawayGames` and `winReasons.RUNAWAY`, never in `stalls` (Python
  counted them as stalls). `report.runaways` still names what was stopped.
  - **For the web session:** `webapp/src/results.ts` passes `stalls`
    and `win_reasons` through; `runawayGames` isn't in the payload yet, and
    main.ts's note only mentions stalls. Worth a line such as "N games
    stopped as runaways (Krenko, Mob Boss)".
- **Cheaper steps on wide boards (layers, this commit), exact:** an
  effect is applied only to the objects its filter names (as the
  dependency probe already did), the dependency search pairs an effect
  only with effects that change what it can reach, and each pair walks the
  smaller side. Same digests on 26 games (0-23, 102, 117, and game 9's
  b30cd7d31ff179bb). Those 26 games: 104 s -> 84 s in all; game 9 67 s ->
  41 s. What's left in game 9 is board recomputation itself (each token
  invalidates the board; broad effects such as anthems are matched against
  every object each time), which needs an incremental board, a larger
  design change.
- Next (the auditor's (c), after the open findings): make a step cheaper
  on wide boards, then raise the cap. Leads: the parser's profile (per-token
  trigger collection recomputes the board; the bot's lookahead clones the
  big board).

## Rules findings in TS: triage and fixes (ROADMAP "Now, in parallel")

Tests: `engine/test/rules/audit_triage_real.test.ts` (real cards; 44
fixtures added to `schema/fixtures.txt`) and `audit_findings.test.ts`
(hand-written IR). Fixes are logged in AUDIT_LOG.md under "TS fixes".

**Fixed now, in TS:** F28 overload (b28f152), F62 MOVE_ZONE to the library
(3ceafeb), F64 VOTE (3ceafeb, marker out), karoo lands (c4232df: Coral
Atoll-style "unless you return a land" could never be paid).

**Already fine in TS** (fixed in Python before the port), each with its
test:
- F8 modular by LKI: "F8" (Arcbound Worker's counter reaches Ornithopter).
- F15/F19 granted triggers: granted_triggers_real (Abnormal Endurance).
- F22 granted undying/persist return once: granted_triggers_real (Mikaeus),
  audit_triage_real "F22" (Rattleblaze Scarecrow).
- F27 protection from a colour: "F27" (Bolt targets White Knight).
- F29 "you may pay. If you do": pay_if_you_do_real.
- F30 simultaneous deaths: "F30" (Damnation + Blood Artist + 4 Bears: 5
  triggers).
- F32 can't be regenerated: "F32" (Wrath of God vs Drudge Skeletons).
- F35 Doubling Season and loyalty: "F35" (Liliana Vess enters with 10).
- F36 Rest in Peace: pay_if_you_do_real.
- F41/F52 cast triggers and intervening-if: "F41/F52".
- F42 goad ends: "F42".
- F51 N targets: target_counts_real (Wicked Pact, Ulamog).
- F53/F53b player protection, hexproof: "F53/F53b".
- F54 echo, cumulative upkeep: "F54" (unpaid: sacrificed; accepted: the
  Mountains are tapped).
- F55 Sly Instigator goads: "F55".
- F56 Force of Will's alternative cost: alternative_costs_real.
- F57 bestow needs a creature: "F57".
- F60 Curse attached to nothing: "F60" (fixed as reported; note the Curse
  is cast with no target and attaches to its caster, a separate gap).

**Second batch, all already fine in TS** (audit_triage_real, "second
batch"): F5 jump-start (mana and a discard, then exiled), F6/F7 aftermath
(Dusk from hand only; Dawn from the graveyard, then exiled), F11 crew
can't tap the Vehicle itself, F12 buyback offered (Capsize), F13
Phyrexian life down to 0 (Gitaxian Probe at 2 life), F20/F23 copies
retarget through `chooseNewTargets` (Twincast; SimpleAgent has it), F24
replicate paid twice (Pyromatics), F26 granted conspire
(commander_free_and_conspire_real), F39/F47/F48/F50 convoke and delve
(Siege Wurm from lands + Bears and from 7 Bears; Stoke the Flames with
green Bears refused; Treasure Cruise from 1 Island + 7 cards; Rosemane
Centaur with Watchwolf first), F40 mana spent (Treasure Cruise, 3 Islands
+ 5 delved: 3), F45 emerge needs its colour.
- F16: replicate copies may retarget (`paid_costs.ts` sets
  MAY_CHOOSE_NEW_TARGETS); blitz's draw is a granted ability (`paid_costs.ts`);
  warp is cast from hand only (the IR's builder sets the zone). By code
  reading; no board test.
- F2 (WARN, enlist then the total cost to attack as one): by code reading
  (`combat.ts` declareAttackers: enlist first, then the locked-in total);
  no board test (no enlist card in the fixtures).
- Also fixed with F28: overload is offered with nothing to target (it has
  no targets, CR 702.96b).

Every rules-owned finding in AUDIT_LOG.md is now triaged. Open: none of
the listed rules findings reproduces on the TS engine after the fixes
above. (F38 was the Python Sandbox; F1 Sandbox.put; not engine findings.)

## Bench: 200 games through `rulesEngine` (auditor's ask)

The four bench decks from `schema/samples` (aggro, combo, control, stax),
runSeed 20261114, maxRounds 25, played in Node by `playGame` with
SimpleAgent, games 0-199, with a 90 s per-game watchdog (an `opts.watch`
that throws). Engine at ce0b202. To reproduce one game:
`playGame(setup, index, seedFor(20261114, index))`.

**Outcomes:** 124 LIFE, 44 COMMANDER_DAMAGE, 5 DECKING, 5 STALL_OUT (games
39, 85, 94, 143, 198: the 100-turn cap, i.e. 25 rounds; no loop draw).
No runaways and no loop draws. Median game 1.1 s, p90 2.4 s, slowest
finished game 81 s. Median 50 turns.

**Rerun after the parser's F63 fix (20610f4):** 9 of the 10 now finish
(55, 57, 73, 103, 142, 144, 149, 157, 194). **Game 63 (seed 3756024319)
now hangs instead**: turn 52-54, only ~50 permanents, still going at
90 s. Profile: 92% in declareAttackers -> attackCosts -> `affordable` ->
canPayCost -> planPayment -> canProduceFor -> search -> pays ->
`findPayment` -> `branches`. An unpayable attack tax of 12+ Phyrexian
symbols makes each `findPayment` walk all 2^12 branches before it fails,
the mana planner's search calls it for each candidate, and `affordable`
asks again after dropping each attacker. Python raised here, so this
hang is new with the fix. **For the parser** (mana/payment.ts is
yours): a cheap necessary check before the branch loop would end it,
e.g. fail at once if the pool's total plus what life can cover is less
than the cost's minimum, or prune branches whose life already exceeds
the budget in bulk. I've left payment.ts alone.

**Crashes (before the fix): 10, all F63** (`RangeError: unreasonable number of hybrid
symbols in a cost`, via findPayment <- canPayCost <- affordable, the attack
costs). Games and seeds: 55 (114627385), 57 (253192874), 63 (3756024319),
73 (3349215499), 103 (3740255445), 142 (4085659038), 144 (3216302316),
149 (152809110), 157 (3415405777), 194 (766788920). The parser has it in
mana/payment.ts. **No port-introduced crash** was found.

**Slow games: 12 hit the 90 s watchdog**, every one a token swarm
(Krenko, Mob Boss doubling its goblins): games 9 (1673232466), 27
(1046943031), 33 (1135086036), 40 (3870538508), 47 (3942470300), 70
(734201371), 77 (3471723094), 107 (3215577216), 155 (413110111), 173
(4250891339), 184 (1731394866), 187 (3714759409). They had 800-5800
permanents at turn 37-67. None is a loop: the game keeps advancing,
every step costs more.
- Profile of game 33 (60 s): 70% in `createTokens` -> `prohibited`
  (CR 111.5, each new token is checked against prohibitions on entering)
  -> `active` -> `collect` -> `characteristicsOf` -> `computeBoard`. Each
  token bumps the epoch, so each one rebuilds the restriction list over
  every permanent, and that recomputes the board: O(n^2) per swarm.
  Python's `create_tokens`/`_active` do exactly the same, so this is
  **inherited, not port-introduced** (Python is slower still).
- Game 9's earlier cost, layer dependency ordering with Ogre Battledriver
  on the board, was fixed exactly in ce0b202 (same digests, games 0-8).
  The same O(n^2) on Python's side is what the docstring of
  `_DependencyProbe` in cr613_layers.py describes.
- An exact fix is possible but is a design call, so I have left it for
  you: e.g. batch the CR 111.5 check per createTokens call when no
  restriction on ENTER_BATTLEFIELD is in force (a token that enters can
  itself add one, so the batch must re-check after a token with a static
  RESTRICTION ability). Say if you want it; tokens.ts is the parser's.

## Questions for the auditor

- **Bench (above):** F63's fix turns game 63 (seed 3756024319) into a
  hang in findPayment's branch search: for the parser, see above.
- **Bench (above):** 10 crashes, all F63; no port-introduced crash; 12
  slow token-swarm games, inherited O(n^2) in createTokens' CR 111.5
  check. Want the exact batching fix, from me or the parser?

- Answered (auditor): the VOTE crash is F64; resolve.ts says `KNOWN: F64`
  now. paying.ts's note on a null payment context was also marked F61; it
  is no crash (no caller reaches it), so it is a plain comment now.
- Answered (auditor): no build fingerprint per record; the run's is enough.