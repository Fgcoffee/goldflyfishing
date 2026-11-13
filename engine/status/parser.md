# parser session: status

Update this file with every push: what landed, what's next, and any
**Questions for the auditor** (the auditor reads it, and every commit
message, at each check-in; direct messages to the auditor are refused).

## Done

All on `claude/modest-bohr-yqrqm7`. `npm test`, `npm run typecheck` and
`python -m pytest tests/ir` are green.

- **IR v1** (`70c670b`, `faff6f4`, `5c0016d`), verified by the auditor:
  - Python codec, schema and exporter;
  - `schema/ir.schema.json` (the source of truth), `schema/README.md` and `schema/samples/`;
  - `engine/src/ir/`.
- `ab05438`: a test that the IR's enum tables match `kernel/enums.ts`.
- `2f80fe7`: `schema/usage.json`, a porting order.
- `e7c8c7d`: `mtgfish/ir/store.py`, which reads the exported IR for a deck.

- **Mana core** (this push): `engine/src/rules/mana/`, ported from `cr106_mana.py`.
  - `cost.ts`: ManaSymbol, parse, mana value, colors, plus, increased_by, reduced_by, reduced_by_mana and substitute_x. Symbols are plain data with numeric kind and colors. `fromIR` reads an IR cost (v1 or v2). `costKey` is the value-equality key.
  - `pool.ts`: your `mana_pool.ts`, moved, plus `restrictionPermits` (SpendOnlyOn) and `usableFor`.
  - `payment.ts`: find_payment and can_pay, the same constraint solve with the same deterministic preference order.
  - `ir_value.ts`: your `mana_value.ts`, moved, with the same API on IR costs, now computed through `cost.ts`.
  - The old paths `cr100_game_concepts/mana_pool.ts` and `mana_value.ts` are one-line re-exports, so none of your imports changed.
  - `test_mana.py` is ported case for case (38 tests), plus 4 reduced_by_mana cases checked against Python.
  - One interface note: a payment's restriction context is `PaymentContext {types, subtypes}` (the spell's printed types), or `null` for a bare payability check. Python passed the GameObject and read its printed type line; the caller now builds the context.

- **UTF-8 and the code fingerprint** (this push):
  - Every read and write in `ir_export.py` and `mtgfish/ir/` names `encoding="utf-8"`, and writes use `newline="\n"`. That fixes the Windows cp1252 crash on names like "ō", and the generated files are byte-identical on every OS. An AST test keeps it that way.
  - `index.json` now records `code`, a fingerprint of the parser, rules, card definitions and the IR codec (`mtgfish.ir.store.code_fingerprint()`).
  - `IRStore` treats an export written by other code as stale: it parses live and logs once.
  - Web session: put `code` in your cache keys next to `card_hash`. It's documented in `schema/README.md`.

- **Printed card data** (this push): every card document now has `card`, Python's `CardDef` / `FaceDef` / `TypeLine`, encoded like the abilities.
  - The schema's root is now the card document; the face document is `$defs/FaceDocument`.
  - `verify --schema` round-trips every printed card too: 42,027 faces of 38,800 cards, 0 problems. Samples are refreshed.
  - Rules session: it matches `kernel/card.ts` field for field, plus three fields you don't have: `edhrec_rank`, `scryfall_uri` and `attraction_lights` (CR 717.1, Attractions). Add them to `CardDef` or ignore them.
  - Until IR v2 lands, its enums (layout, colors, types) are v1 strings like the rest; read them with `enumValue`.

## IR v2 (numeric enums): landed

- Re-landed (this push) after the rules session's `799bcf0`. Enums are numbers, the same numbers as Python and `kernel/enums.ts`, printed card included. Field names are snake_case, a plain int in an enum-typed field is `{"$int": n}`, and `$str` is gone.
- `verify --schema`: 42,027 faces of 38,800 cards and every printed card round-trip and validate, 0 problems. Samples and `usage.json` are refreshed.
- `enumValue()` and `irString()` still accept both forms.
- **Web session:** the default export path is now `cache/ir/v2/` (`ir_version` 2). Point `MTGFISH_IR_DIR` and the `/ir/v1/...` URLs at v2, or serve both. The card files' shape is unchanged apart from numbers in place of enum strings, and the new `card` key.

## Mana planning (this push): the core is in, the game side needs you

`engine/src/rules/mana/plan.ts` ports `mana_plan.py`.
- **Done, game-independent:**
  - `planPayment({pool, life, cost, sources, context})`: the same search, with cheap single-purpose sources first, ceilings for pruning, the search budget, and trimming of unneeded activations. It keeps one life point back.
  - `sortSourceOptions`; drawbacks read from the IR (`costDrawback`, `effectDrawback`, `optionDrawback`, `modalEffect`).
  - `executePlan`, with the **F61 guard**: every source is re-checked through the host before it's activated, and `PlanNoLongerHolds` is thrown if one has gone.
  - Tests rebuild the Python board scenarios as options: pain land, basic before pain, Signet, any colour, only what's needed, Phyrexian life, and the F61 re-check.
- **Rules session, please implement `ManaPlanHost<Game>`** (in `plan.ts`). It's the part of `mana_plan.py` that needs casting and resolution, which are yours:
  - `manaOptions(game, player)`: Python `mana_options` with `_outcomes` / `_produced` / `_predict`. For each untapped permanent's mana abilities that `_can_pay_activation`, give one `ManaOption` per mode × colour choice, with `produces` predicted the way `_do_add_mana` would add it, `manaCost` from `activation_mana_cost`, `drawback: optionDrawback(ability, modes)`, and each source's list `sortSourceOptions`-ed.
  - `stillActivatable(game, option)`: Python `_still_activatable`. The F61 check: on the battlefield, still has the ability (by identity), and can still pay.
  - `activate(game, player, option, ability)`: `_pay_activation` plus `resolve_mana_ability` (modes via `choose_modes`, `mana_color` = `option.color`), then `resolve_mana_triggers`.
  - Then `canProduce(host, game, player, …)` and `executePlan(host, …)` are Python's `can_produce` and `execute_plan`. Tell me if you'd rather I write the host myself once casting lands.

## Matching, values and conditions (this push): `engine/src/rules/match/`

Ported from `kernel/matching.py`, `values.py` and `conditions.py`: `matches`, `find`, `resolvePlayers`, `valueSubjects`, `evaluate`, `holds`, plus `linkedObjects`, `partnerOf`, `isModified`, `hasInset`, `commanderColorIdentity` and `partySize`. Every IR field is read with its default, through your `ir_read` helpers.
- **Hooks:** importing `rules/match` installs everything into `kernel/hooks.ts`; `installMatch()` re-installs it. Rules/web session: the engine's entry point (or the game runner) has to import `rules/match` once, so the hooks are live outside tests. The layer tests' `Board` still installs its stand-in matcher in its constructor.
- **Tests:** 21. They port the owner-filter, any_of, filter-bound and history-condition cases from Python, plus value cases (count, superlatives, devotion, floor division, remembered filters) and condition cases (controls, and/or/not, existential vs each, count vs amount, last-known "if it was", counters).
- **What I read from your state, and what's missing:**
  - Combat is read through `CombatView` in `match/matching.ts`: `attacking`, `attackingPermanent`, `blockers`, `blocking`, `wasBlocked` and `blockersDeclared`, as Python's `Combat` has them (Maps and Sets). Until `game.combat` has that shape, every combat question fails closed.
  - **PENDING(rules)**, failing closed until the kernel keeps the record:
    - `FIRST_RESOLUTION_OF_NAME` (Python `game.spell_resolutions`);
    - `WAS_KICKED` with a named rider (`paid_additional_keywords`);
    - `COMBAT_DAMAGE_DEALT_BY` and `COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE` (the combat-damage-by record).
  - **KNOWN:** `COST_PAID` / `COST_PAID_POWER` read an object the payment moved through its `lastKnownCharacteristics`, because the TS Game has no `cost_paid_last_known` yet.

## Bracket classification (a073ae9): the parser's half of the estimated bracket

Every card document now has `bracket`: what the card counts as, from `game_changer`, `tutor`, `extra_turn`, `mass_land_denial` and `unread`. It's in exported files, live parses and the samples. `mtgfish/ir/bracket.py` reads them from the IR, not from oracle text or Scryfall's tags.
- **tutor**: a library search for a card that needn't be a land. Land-only searches (Cultivate, fetch lands, "Forest or Island card") are ramp.
- **mass_land_denial**: destroys, exiles, bounces or makes players sacrifice lands without targeting:
  - all of them ("all lands", "all permanents", Jokulhaups);
  - all of a land type (Boil, per the official definition: four or more lands per player);
  - two or more, or X, from each player (Death Cloud, Destructive Force).
  Not recognised yet: static denial (Winter Orb, Blood Moon), triggered denial (Burning Sands), storm copies of a targeted spell, and gaining control of a player's lands.
- **game_changer**: Scryfall's `game_changer` flag (the official list, 53 cards), from the card dump. The list's hash is part of `code_fingerprint()`, so a new list makes the export stale.
- **unread**: part of the card wasn't read; its other tags are a floor.
- **Deck estimate:** `estimate(documents)` gives a bracket from 2 to 4, with reasons, caveats and the cards behind each count.
  - More than 3 Game Changers, or any mass land denial: 4. Otherwise 1-3 Game Changers, more than 3 tutors, or more than 2 extra-turn cards: 3. Otherwise 2.
  - It never says 1 or 5: those are about intent and tuning, which a list doesn't show.
  - The thresholds for "few" tutors and "low quantity" of extra turns are mine. The official descriptions give no numbers.
  - Web session: it's a suggestion. Use it, or count the tags yourself.
- **Cross-check against Scryfall's tags**, `ir_export brackets` (written to `schema/brackets.json`), over 32,765 format-legal cards:

  | Tag | Agree | Only the IR | Only Scryfall, read | Only Scryfall, unread |
  |---|---|---|---|---|
  | tutor | 253 | 38 | 1 | 265 |
  | extra_turn | 0 | 0 | 0 | 57 |
  | mass_land_denial | 29 | 2 | 12 | 68 |

  Nearly every miss is a card the parser can't read, which the `unread` tag flags.
- **Findings (card readings, deferred until after the port):**
  - **The parser never emits EXTRA_TURN.** All 57 extra-turn cards (Time Warp included) are unread, so the extra-turn count is always 0 today and those cards carry `unread`.
  - **Learn is read as "search your library for any card"** (`cr701_keyword_actions.py`, the "Learn" builder). It should be a Lesson from outside the game, or discard-and-draw. Most of the 38 "only the IR" tutors are Learn cards, so a Strixhaven deck's tutor count is inflated.
- Checks: `pytest tests/ir tests/web` 176 passed; `npm test` 234 passed; typecheck clean; `verify --schema --sample 400` 0 problems.

## Hooks: resolvePlayers and defendingPlayers (869a1b1)

- Rules session (your d10563c): `installMatch` in `match/index.ts` now installs `resolvePlayers` and `defendingPlayers` too. A test checks both are reachable through `matchHooks()`.

## State-based actions (ff39b13): `rules/cr700_additional_rules/sba.ts`

Phase 2 (brackets) is on hold, per the auditor. SBAs, setup and copies/faces/tokens moved to me (084182b).
- `checkStateBasedActions(game)`, replaced in place, so your `settle()` and cleanup already call it.
- It covers:
  - players (CR 704.5a-c, 903.10);
  - the commander zone choice (CR 903.9a), through the agent's `moveCommanderToCommandZone` as in Python;
  - toughness 0, lethal damage, deathtouch and regeneration (your `destroy`);
  - planeswalkers;
  - battles: defense, the Siege exception and protectors;
  - Auras: illegal host, enchant player, protection, and bestow's exception;
  - Equipment and CR 704.5p;
  - counter annihilation, keeping last-known counters (CR 704.8);
  - tokens and copies ceasing to exist, with the CR 722.3c exception;
  - the legend, world and Role rules;
  - start your engines!, storied, and broken soulbond pairs.
- 49 Vitest cases, from the Python board tests.
- **Rules session, still yours:**
  - `PORT:` `check_removal_from_combat` at the top of each round (CR 506.4), with combat.
  - `isBeingAttacked` reads `game.combat.attackingPermanent` (Map attacker -> attacked permanent, as Python's `attacking_permanent`). Until combat has it, nothing is under attack.
  - Dungeons (CR 704.5t) wait for cr309.

- Legend rule and CR 101.1: done (535e083). `checkLegendRule` calls your `ruleIsSuspended(game, Rule.LEGEND_RULE, { obj })`, with the Python test.

## Game setup (bfd46b2): `rules/cr100_game_concepts/setup.ts`

- `setUpGame(decks, { seed, logEnabled, abilityProvider, relaxations, randomizeTurnOrder })` is Python's `cr103_setup.new_game`. It builds a game up to its first turn:
  - creates libraries and commanders (CR 903.6), with starting deck names (CR 903.11a), the sideboard outside the game, and `sourceDecks` (CR 727.2);
  - rolls turn order on the seeded PRNG (CR 103.1, 806.3), shuffles every library, sets life to 40 and draws seven (CR 903.7).
- A deck is `SetupDeck { name?, library: CardDef[], commanders?, sideboard?, companion?, attractions? }`.
- `takeMulligans(game, policy, { maximum })` is the London mulligan (CR 103.5) and returns a `Map` of mulligans taken per player. `keepSeven` and `simpleLandPolicy` are Python's two policies.
- 11 Vitest cases from `test_engine_core.py`: setup, determinism and mulligans.
- **Rules session:**
  - `play(setup, index, seed)` can call `setUpGame` then `takeMulligans`.
  - The companion is revealed at setup through your `revealCompanion` (9579666).
  - The Attraction deck (CR 717.2, 103.3a) waits for the Attractions port.

## Copies, faces and tokens (80d8ede, 38f0866)

- **Tokens** (`rules/cr100_game_concepts/tokens.ts`, 80d8ede): `createTokens(game, spec, controller, count, { source, card })` and `createEmblem(game, controller, abilities, { text })`, with the names you asked for.
  - A token gets a synthetic card, which your `printedCharacteristics` already reads.
  - The count goes through `replacementHooks().replace` first (CR 614.1c), with the spec in `data`.
  - CR 111.5 (can't enter, never created) and CR 800.4b (departed player) are handled. Double-faced tokens work (CR 111.10i).
  - **Rules session:** `PORT:` `enters_attacking` (CR 508.4) waits for your combat (`enter_attacking`).
- **Faces and copies** (`rules/cr700_additional_rules/faces.ts`, 38f0866):
  - `transform` works in place (CR 712.18), with `canTransform` and `canEnterTransformed`.
  - `copyPermanent` adds a layer 1 continuous effect.
  - `copySpell(game, original, { controller, mayChooseNewTargets })` copies targets, modes and X; the copy isn't cast and is owned by its controller.
  - `chooseNewTargets` goes through the agent's `chooseNewTargets(game, controller, copyId, current, candidates)`; `MAY_CHOOSE_NEW_TARGETS` is exported.
  - Your `layoutOf`, `castableFaceIndices` and `playableLandFaceIndices` are kept unchanged.
  - **Rules session:** the TRANSFORM, COPY_PERMANENT and COPY_SPELL executors can call these now. `copySpell` takes keyword arguments as an options object, like Python's `controller=` and `may_choose_new_targets=`.
  - `zones.ts` has its own private `canEnterTransformed`. You could import mine instead, but it's not urgent.
- 18 + 23 Vitest cases.
- **Not in this item:** the rest of cr708 (manifest, cloak, turning face down) and the CR 718.2 prototype face, which is still KNOWN in layers. Say if you want either from me.

## Next

- Every Phase 1 item but one is done, the three moved from rules (084182b) included. Phase 2 (brackets) is on hold until the port is done, per the auditor.
- **Last item: board tests for every module I own.** SBAs, setup, tokens and faces/copies were ported with their modules (50 + 13 + 18 + 23 cases); `test_mana.py` is ported in full. Still to port, now that your mana host and casting exist:
  - mana/: `test_cr601_mana_plan.py` (8), `test_mana_plan_stale_source.py` (3, done: 782379a), `test_cr605_modal_mana.py` (5), `test_entering_and_mana.py` (7), `test_cr903_commander_identity_mana.py` (2).
  - match/: `test_filter_bounds.py` (8), `test_cr108_owner_filters.py` (7), `test_object_filter_any_of.py` (4), `test_history_conditions.py` (12), `test_cr608_affected_values.py` (23), `test_cr608_values_this_way.py` (7), `test_cr702_dethrone_condition.py` (5). Today match.test.ts has a sample of these (21 cases).
  - The few cases that resolve a spell wait for your resolution executors.
- Then the PENDING(rules) records in `match/` as you add them, and the IR side of anything the port turns up. Card-reading fixes wait until after the port.

## Questions for the auditor

- None. The two bracket questions (combo list source and licence, and the thresholds) are parked for the user at Phase 2, as you said.
