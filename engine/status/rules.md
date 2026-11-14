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
  resolve.ts. BLOCKS_SOURCE_IF_ABLE is done too (1ed92e2). **Waiting on you (parser):** TURN_FACE_DOWN and MANIFEST need
  `turnFaceDown`, `manifest`, `manifestDread` (cr708); VENTURE needs cr309's `venture`; RING_TEMPTS and
  SACRIFICE_BLOCKERS_AT_END_OF_COMBAT need `tempt` and
  `sacrificeBlockersAtEndOfCombat` (cr701_ring); OPEN_ATTRACTION and
  ROLL_TO_VISIT need the Attractions functions. Until then those are logged
  and skipped. actions.ts has the damage and
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
1. Resolution executors: done except the seven waiting on parser modules.
2. Triggers: done.
3. Combat: done.
4. `play(setup, index, seed)`: done; the web session can swap it in.
5. The build fingerprint, then board tests.
(Moved to the parser: SBAs, setup, copies/faces/tokens 084182b;
replacements and prevention, commander rules, the in-game CR 700
mechanics 18f9e4f.)

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

- combat.ts inlines `isARingBearer` (cr701_ring.is_a_ring_bearer, yours,
  not yet published). When you publish the Ring module I'll import it.
- turn.ts's CR 727 restart reads the card catalog off the game by cast
  (`cardCatalog`), as digital_mechanics.ts stores it. A `cardCatalog(game)`
  getter there would be tidier; say if you add one.

## Questions for the auditor

- **A Python crash, guarded:** `_do_vote` calls
  `game.apnap_order(game.next_player(...))`, but `apnap_order` takes no
  argument, so every VOTE raises TypeError in Python. Guarded in resolve.ts
  with the evident intent (turn order from the next player) and marked
  `// KNOWN: F61`, as the port rules say for crashes. It may deserve its
  own finding number. (The MOVE_ZONE-to-library bug is F62, marked in resolve.ts.)
