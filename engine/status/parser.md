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

## Top priority from 16 Nov: the card database read to nearly 100%

**Numbers on the new snapshot (2026-11-15); latest column 22 Nov** (census; the pool is every card legal in some format):

| | 16 Nov, before | 16 Nov, after | 22 Nov | of |
|---|---|---|---|---|
| abilities | 44,694 (70.4%) | 44,747 (70.5%) | 44,776 (70.5%) | 63,494 |
| cards, whole pool | 17,520 (53.0%) | 17,556 (53.2%) | 17,582 (53.2%) | 33,032 |
| top 100 by play rate | 96 | 97 | 97 | 100 |
| top 1,000 | 817 | 824 | 826 | 1,000 |
| top 5,000 | 2,815 | 2,833 | 2,847 | 5,000 |

"Read" counts recognised dummies (below) as read.

**"It" in a static ability fixed (from the readings queue).** My b2ce87b batch read "As long as this creature is equipped, it has double strike" with "it" as whatever was just referred to; a static ability has nothing referred to, so every permanent got double strike (the web agent confirmed it on a board). Now, where "it" is the first thing acted on in a static ability or a step trigger, it binds to the object the condition is about: the source ("this creature is equipped") or what the source is attached to ("enchanted creature is blue"). With no such object, a static ability is left unread. Census on the new pool: 37 readings changed, all "it" now bound (including older misreads: Clout of the Dominus, Narcolepsy, Mana Bloom, Pristine Angel, Arixmethes); 2 lost, Deadly Wanderings and Homicidal Seclusion ("that creature" for the one creature you control), now unread instead of buffing everything. The Kor Duelist board test now checks a Grizzly Bears beside it gets nothing.

**The failures among the top 1,000** (`play_rate_report --depth 1000`): 170 abilities in 154 clusters. It's a long tail: no shape covers more than 3 played cards, so the target (95% of the top 1,000) needs the dummies as well as grammar rules. Top shapes: "the next spell you cast this turn", "untap during each other player's untap step", opening-hand Leylines, "the color of your choice", "if this card is in your graveyard", mana-spent riders, delayed triggers on a countered spell (Mana Drain, Arcane Denial), "share a land type".

**Dummy abilities: in** (0100e79).
- `EffectKind.DUMMY` (998), additive. `rule` names the shape, `text` is the words. The engine logs it as unimplemented and skips it (`engine/test/rules/dummy_abilities.test.ts`).
- The registry is `mtgfish/parser/dummies.py`. A **whole** dummy is the entire ability. A **rider** is the last sentence(s) after a part that reads; the readable part keeps working (Path of Ancestry taps for mana; only its scry rider is a dummy).
- Matched only against abilities the grammar didn't read, on the whole text, so no current reading can change. Census (old pool): +40, -0, 0 changed.
- `python -m mtgfish.tools.dummy_report CENSUS.jsonl.gz` lists each shape with its cards (all, top 1,000, top 5,000): the rules session's backlog.

| dummy (new pool) | top 1,000 | top 5,000 | cards |
|---|---|---|---|
| untap-during-others-untap-step | 3 | 6 | 8 |
| begin-game-on-battlefield | 2 | 8 | 18 |
| next-spell-rider | 2 | 3 | 8 |
| mana-spent-trigger | 1 | 6 | 8 |
| mana-not-lost | 1 | 3 | 13 |

**Since (1b04e2e):**
- A whole dummy on an activated ability stays activated with its cost (Mistrise Village, Sloppity Bilepiper); the bot doesn't pay to activate an ability that only has dummies.
- "This ability costs {1} less to activate for each ..." (Boseiju, Otawara, Takenuma, Pteramander and 24 more) is a dummy, `activation-cost-change`, carrying its amount, instead of an effect that ran on resolution. Census: 0/0, 33 changed in all.
- "This spell can't be countered" is a static ability on the stack (47f80ac; 52 cards), and "every basic land type" adds all five (3 cards). Rishkar's Expertise answered in the queue (not a misread).
- **Engine fix, rules session please look (e2bce20):** `applySelfEntryReplacements` (replacement.ts) tapped every "enters tapped unless ..." land without asking the condition: 116 read cards, most played duals among them, always entered tapped. It now walks only the branch whose condition holds. Board test: Mystic Sanctuary. Every bench and browser-run digest with those lands will change.
- - **22 Nov (88e6a7a, 91575eb):** "costs {X} less, where X is ..." keeps its reduction (The Great Henge and 18 more; engine test); "create a tapped ... token" enters tapped (42 cards); the snapshot's new keywords and ability words are registered, so `test_keywords` is green again (+10 cards, the Seek out ability word). Full Python suite: 3733 passed, 1 failed (`test_cancel`, the known timing flake under `-n 4`). Engine 955.
- **Extra triggers check their causing event (f51ee2e):** "if a creature *dying* causes ..." (Teysa, Panharmonicon, Yarok, Drivnod and 8 more) dropped the event, so Teysa doubled entering triggers too. Parser reads the event; both engines check it. Census: 12 changed, 0/0; Wayta's "being dealt damage" is unread now. Rules: Felix Five-Boots' cause is matched against the damage recipient (audit log).
- **Julian Bashir (0047f5f):** the web agent asked; it was a misread ("its power" in a target filter read each candidate's own power). Fixed; 1 card.
- **Paid casts (89cb727):** "you may cast that card" was free (Ragavan, Yawgmoth's Will and 17 more); now paid. "Cast this spell only during" is unread rather than a self-cast.
- **For the rules session, found today:** "that player" / "they" in an object filter (owner/controller SPECIFIC, no player) matches nothing in either engine: 110 read cards, `audit/unbound_that_player.md`, board-checked on All Is Dust and How to Start a Riot. Proposed binding in the audit log.
- **Theros gods (e08b81f):** "this isn't a creature" below devotion: +16 cards. Read-back now names card types instead of a number; golden entries for Dryad, Roaming Throne, Urborg and Yavimaya refreshed (wording only), web session please note.
- **Branch of Vitu-Ghazi** answered in the queue: two disguise abilities by design (CR 702.37a).
- **Readings lock (e2faf02), the auditor's (a):** `schema/readings.lock`, one line per format-pool card (oracle id, read/partial/notext, a hash of its IR abilities, name), 33,399 cards. `tests/ir/test_readings_lock.py` fails when any reading differs; `python -m mtgfish.tools.readings_lock write` updates it in the same commit, and its diff names the cards that moved. About 80 s of the Python suite. Next: (b) per-card authored readings, then "choose target then branch" and "any one player, first to pay" (both decided yes).
- **Authored readings (a7346cc), the auditor's (b):** `mtgfish/ir/authored/<oracle_id>.json`, served by the export in place of the parse and marked `"authored": true` on the face (schema: optional, additive). Written with `python -m mtgfish.tools.authored write NAME --as F.I "<paraphrase>"`: the paraphrase is a wording the parser reads, kept beside the IR for review. `tests/ir/test_authored.py`: current text, decodes, validates, served, and flags a paraphrase that now reads differently. First card: **Cabal Ritual** (engine test: 3 black, 5 with seven cards in the graveyard). The lock shows it as `authored`. Many of the top-1,000 one-offs need IR the engine lacks (Mother of Runes' "color of your choice"); those are dummies or grammar-plus-engine work, not authoring.

**Numbers (28 Nov):** top 100: 97; top 1,000: 826; top 5,000: 2,847; pool: 17,582 read + 1 authored of 33,032.
- **For the rules session (140eef5):** "target player controls" now targets the player: Primeval Light, How to Start a Riot and 38 more. Public Execution's "that player" = the target's controller is still open on my side. Living Death: done (7ce7522).
- **Dummies the engine carries out (ded34bb):** the three in `IMPLEMENTED_DUMMIES` are no longer listed as gaps (parseStatus, deckview); the read-back says "done by the engine".
**For the auditor, a choice:** the mana abilities that should be colour-limited (Exotic Orchard, Fellwar Stone, Reflecting Pool, Chrome Mox, Mox Amber, Bloom Tender) read as "any colour". The engine can't compute the limit yet. Honest options: (a) leave them making any colour, flagged as a known overstatement; (b) make the restriction a dummy, which leaves the mana ability unable to tell what it may make, i.e. inert, so Exotic Orchard (rank 4) would make no mana. I've left them as they are until you say; (a) changes no game, (b) changes many.

**For the web session:** `parseStatus` (`engine/src/sim/payload.ts`) now returns `recognised`, the dummy names on a card, beside `status` and `unread`. A card whose only gaps are dummies has status "ok" and a non-empty `recognised`; the site can show "recognised, not implemented yet" for it. In the IR, it's an effect with `kind` 998 and its `rule`.

**Card data refresh: landed** (38875b6, another agent). I rebuilt my card database from the new dumps (`fetch_scryfall --offline`) and the numbers above are on the new pool. `engine/status/readings_queue.md` is part of my queue: the "it" entries are fixed in this batch; the mana-colour restrictions, the channel lands' cost reduction, and the rest are next. Also added an English phrasing for REPEAT_PER_PAYMENT (datarefresh.md's explain failure). **Rules session:** it shows Imperial Hullbreachers' squad as "create 0 copy ... token(s)" per payment; the count comes from the keyword expansion (cr702_keyword_impl.py), so worth a look.

## The user's urgent list (15 Nov): History, Replay art, reports

Asked for by the user through the auditor (ROADMAP 6cb515a), ahead of card readings. Each was checked in headless Chromium against a local server with `?engine=browser`, as well as by tests.

- **History** (cf771e8). A browser run is named as a server run is: the saved deck and version holding the hero's list, else the Archidekt deck's own name (`resolve_decklists` now returns `names`), else the commander. Its win rate is the hero's wins over recorded games, as the report computes it. Both are stored at upload (migration 0005); runs stored earlier get them the first time they're listed.
- **Replay art and the card window** (ef2db75). A new bridge call, `card_pictures`, gives each card's art, image and printed text by name (tokens included), the lookup `_board_cards` uses. Browser replays fill their board from it. The Replay card window, for every run, now shows the printed text and the per-ability reading (`sandbox_inspect`, as the Deck drawer does).
- **Reports** (ad96101, 2f60213). "Report a problem" in the top bar on every page; "Report this reading" in Replay's card window, the Deck drawer and the Sandbox card panel; "Report this step" in Replay. A Reports tab for admins (`MTGFISH_ADMIN_EMAILS` with a verified email, or `MTGFISH_ADMIN_DISCORD_IDS`): filter by status and kind, set the status and a note. Reporters see the status in their account window. Anonymous allowed; rate limited; export, deletion, 12-month purge of anonymous ones, and the Privacy Policy. Migration 0006.
  - **Deploy:** set `MTGFISH_ADMIN_EMAILS` on the server, or nobody sees the Reports tab.
  - **Not yet:** opening the replay at a reported step from the review page (an admin can't open someone else's browser run today), filtering by card, a report button on Results' card-impact chart, and Sandbox step reports (they wait for the sandbox on TS).
  - The Privacy Policy changed (reports are new data). I didn't bump `TERMS_VERSION`, which would ask everyone to accept again: the user's call.

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

## Your records, wired (b658801)

- `match/conditions.ts` now uses `spellResolutions` (FIRST_RESOLUTION_OF_NAME), `paidAdditionalKeywords` (a named WAS_KICKED rider) and `combat_damage_record.ts` (both combat-damage conditions). No PENDING(rules) or KNOWN is left in `match/`: COST_PAID reads your `costPaidLastKnown` for an object the payment moved (d31bdf4).
- **Rules session:** `copyTokenCard(game, original, spec)` is exported from `tokens.ts`, a port of Python's `copy_token_card`. Import it and drop the private copy in `resolve.ts`. Tokens with `enters_attacking` now call your `enterAttacking`.

## Replacement and prevention (6e05490): `rules/cr600_spells_and_abilities/replacement.ts`

The auditor moved three more items to me (18f9e4f): replacements, commander rules, and the in-game CR 700 mechanics. Replacements came first because your resolve.ts needs them.
- **Rules session: the three you're calling through stubs are published, with Python's signatures (keyword arguments as an options object):**
  - `register(game, effect: ReplacementEffect): ReplacementEffect`;
  - `replacementFromEffect(effect: Effect, { source, controller, condition?, duration?, createdTurn? }): ReplacementEffect | null`;
  - `becomeChosenCharacteristics(game, obj, effect)`, which calls your `execute(newResolution(...))`.
  - Also exported: `ReplacementKind`, `ReplacementEffect` (camelCase fields), `newReplacementEffect`, `applyReplacements`, `staticReplacements`, `clearExpired`, `hasReplacements`, `commanderZoneReplacement`, `wantsCommandZone`, `tokenSpecMatches`, `amplifyReveals`.
- **Hooks:** importing the module installs `hasReplacements`, `replace`, `commanderZoneReplacement` and `applySelfEntryReplacements` into your `replacement_hooks.ts`. Your existing `zones.ts`, `actions.ts` and `tokens.ts` calls go through those unchanged.
  - Python's `Game.static_replacements_cache` is a per-game cache inside the module, so `Game` needs no new fields.
  - `play()` imports `rules/match` and `replacement` (50fcbfb), so the hooks are live in real games.
  - `junkyardReplacement` and the `aura*` hooks are left as they are: the junkyard comes with Attractions (mine, CR 700), and the Aura ones look like yours (cr303).
- `preparation.ts` ports cr722 (`becomePrepared`, `keepsItsPrepareCopy`).
- 18 Vitest cases: static and registered replacements, doublers (CR 614.5), prevention and shields, damage modification, redirect-on-death, CR 903.9b, taplands, untapped-beats-tapped, enters with counters.
- **Your digital_mechanics question:** yes, it's in my CR 700 list (18f9e4f, "attractions, preparation and digital mechanics"). I'll publish `seek`, `conjure` and `perpetually` under Python's names.

## Commander rules (d5dc038)

- The CR 903 rules work together end to end, through your `castSpell`/`totalCost` and `dealDamage` (`rules/cr903_commander/commander.test.ts`):
  - the command-zone choice: `wantsCommandZone`, the CR 903.9a SBA and the 903.9b replacement;
  - tax across a trip home (CR 903.8);
  - commander damage across zone changes (CR 903.10a, 704.6c).
- **Rules session, for your combat port:** `dealDamage` counts commander damage only with `isCommanderSource: true`. Python's combat passes `is_commander_source=source.is_commander` (cr506_combat.py:1138), so please do the same, or no commander ever kills.

## MTG Arena mechanics (9ad1ad5): `rules/cr700_additional_rules/digital_mechanics.ts`

- **Rules session:** `seek(game, player, wanted, count, { source, controller, toZone })`, `conjure(game, player, card, zone, { tapped, fromTop, source })`, `conjureDuplicate(..., { keepPerpetual, tapped, source })`, `catalogCard`, `conjuredName` and `perpetually(game, obj, effect, controller, { amounts })` are published. Your SEEK, CONJURE and PERPETUALLY executors can call them.
- **Three KNOWNs of yours this unblocks:**
  - `zones.ts:181`: call `carryPerpetual(game, obj.id, created.id)` at a zone change.
  - `layers.ts:375`: bring `perpetuallyChanged(game)` into the computation and add `perpetualContinuousEffects(game, byId)` to the layer inputs.
  - The card catalogue: `setCardCatalog(game, catalog)`, wherever `play()` builds the game.
- `Game` has no `perpetual` or `cardCatalog` field, so both are set on the game object by this module. The perpetual record is copy-on-write, so `cloneGame`'s spread shares it safely. If you'd rather have real fields on `Game`, add them and I'll switch.
- `conjure` repeats two small private pieces of `zones.ts`: `mayEnterTheBattlefield` and the entry part of `emitZoneSpecific`. If you export them, I'll import them instead.

## Soulbond (de31695), and keyword expansion confirmed

- `soulbond.ts` has `pair(game, first, second, controller)` for your PAIR executor, plus `isStillPaired`, `partnerOf` and `endBrokenPairs`. `sba.ts` and `match/` now use it.
- **Note:** 8655b15 imported `soulbond.ts` before the file was committed, so the branch didn't build from 8655b15 until de31695, which adds the file. If you pulled in between, pull again. The head is verified on a clean checkout: typecheck and 474 tests pass.
- **Keyword expansion is at export, as the auditor asked me to confirm:**
  - `cr702_keyword_impl.py` and `cr701_keyword_actions.py` run inside the parser, and the IR carries the abilities they build.
  - No Python game-time code calls them except `cr708_face_down._ward_two` (Ward {2} for disguise and cloak), which your `layers.ts` already builds by hand.
  - Token keywords become bare keyword abilities, as in Python (`tokens.ts`).
  - So neither module is ported.

## Designations (928b7e9) and dungeons (e25664c)

- **Designations** (`designations.ts`, replacing the placeholder in place):
  - the monarch, the initiative, day and night, rad counters;
  - `takeInitiative` is published for your TAKE_INITIATIVE executor;
  - `combatDamageToMonarch(game, victim, attackerController)` and `combatDamageToInitiativeHolder(game, victim, attackerControllers)` are for your combat to call when combat damage hits a player.
  - The initiative's inherent triggers are queued with your `pendingTrigger(NO_OBJECT, ability, event, controller)` (e093445).
- **Dungeons** (`rules/cr300_card_types/dungeons.ts`):
  - `venture(game, player, quality)` is published for your VENTURE executor;
  - `completeDungeon`, `currentDungeon` and `finishedDungeons`;
  - `sba.ts` now does CR 704.5t.
  - Python parses dungeon room effects at game time from the card database, so I export the dungeons as IR instead. `ir_export dungeons` writes `schema/dungeons.json` and the generated `engine/src/ir/dungeons.ts`, and a Python test keeps both current. The engine needs no card database for dungeons.
  - Room abilities trigger on VENTURE_MARKER_MOVED (amount = room + 1), from the command zone, with `trigger.room` set: your triggers port matches the room there, as Python does.

## The Ring (e093445), face-down (4fd5043), Attractions (1a047e2)

These were the last things your resolve.ts was skipping for me.
- **The Ring** (`ring.ts`): `tempt(game, player)` and `sacrificeBlockersAtEndOfCombat(game, attackerId, controller)`, which adds one of your `delayedTrigger`s. Also `ringAbilities`, `ringBearer` and `isARingBearer`.
  - Your `combat.ts` has a private inlined `isARingBearer`; you can import mine.
  - `sba.ts` now calls your `checkRemovalFromCombat` at the top of each round, as Python does.
- **Face-down** (added to your `face_down.ts`): `manifest(game, player, card, how)` (cloak is `how = "Cloak"`), `manifestDread`, `turnFaceDown`, plus `mayLookAt`, `characteristicsSeenBy`, `faceDownOrder`, `revealAll` and `isHidden`. I see you already wired TURN_FACE_DOWN and MANIFEST (26565d5).
- **Attractions** (`attractions.ts`, replacing the placeholder in place): `openAttraction(game, player)` and `rollToVisit(game, player)` for OPEN_ATTRACTION and ROLL_TO_VISIT; your turn.ts already calls `rollToVisit` and `controlsAnAttraction`.
  - The CR 717.6 junkyard replacement is installed into your `junkyardReplacement` hook by `replacement.ts`.
  - `setUpGame` builds and shuffles each player's Attraction deck from `SetupDeck.attractions`.
  - **One call for zones.ts** (your KNOWN at the CR 717.6a comment): after a move into the command zone, call `arrivedInCommandZone(created)` from `attractions.ts`. That puts a junkyarded Attraction in its owner's junkyard pile, as Python's `move_object` does. Until then it reaches the command zone in no pile.

## CR 700 mechanics: done

- `cr702_cost_keyword_rules.py` needed nothing new. Every function already has a TS home:
  - `carry_payment_record` is in `zones.ts`;
  - `times_paid_for`, `do_repeat_per_payment`, `copy_originals` and `do_give_gift` are in `resolve.ts`;
  - `chosen_cost`, `chosen_mode_costs` and `cheapest_mode_cost` are in `total_cost.ts`;
  - `cast_face_index`, `CONVERTED_CAST_KEYWORDS` and `promise_gift` are in `paid_costs.ts`;
  - the combat-damage record is in `combat_damage_record.ts`;
  - `copy_token_card` is in my `tokens.ts`.
  The others were ported by you as you needed them.
- The item is ticked. Keyword expansion stays at export, as above.

## Citation baseline (2695fdf)

- CR 205.3i, cited by `mtgfish/ir/bracket.py`, is in `mtgfish/data/cr_baseline.json`. Only that entry was added; the rest of the re-recorded baseline was left as it was. `tests/rules/test_citations.py` passes.

## AI port (ad1d7cf): `engine/src/ai/`

- `simple.ts` ports SimpleAgent; `lookahead.ts` ports the lookahead. The shadow game is a `cloneGame` with copied agents, no observer, and libraries shuffled by a PRNG seeded from the position (turn, step, log digest), so a projection leaves the real PRNG and log alone (tested).
- 14 tests in `ai.test.ts`, on real cards (Island, Divination and Beast Whisperer added to the fixtures).
- **Rules session:** `play()` now seats `new SimpleAgent()` when `agentFor` gives nothing (`rules/play.ts`), and `play.test.ts` checks it. `rulesEngine` therefore plays the real bot.
- Not ported: `is_continuing` (loop detection), which waits for `loops.ts`.
- Bench, aggro against combo/control/stax, 20 games, seed 0:
  - Python simulate: aggro won 6/19 (31.6%), 2 stalls (10.5%), 1 crash (a `{W/P}`x15 attack cost).
  - TS `play()`: aggro won 8/20 (40%), 1 stall (5%). Wins by seat 8/1/6/4. Endings: LIFE 16, COMMANDER_DAMAGE 3, STALL_OUT 1. 48 turns on average; about 15 s per game in vitest.
  - The win and stall rates are in the same ballpark. Twenty games can't tell a 32% rate from 40%.

## The page plays the real engine (a41d93e)

- `DEFAULT_ENGINE` is `"rules"` (`engine/src/sim/engines.ts`, exported from `@goldfly/engine/sim`).
  - The worker plays `ENGINES[request.engine]`.
  - The protocol accepts any engine by name.
  - The client and pool default to `DEFAULT_ENGINE`.
  - The plumbing tests in `webapp/test/worker.test.ts` pin `"stub"`; a new test plays real fixture cards through the pool.
- Checked in Chromium on the built page (vite preview, the Python server on a fresh IR export), with 3 workers:
  - the default deck in all four seats, 40 games: 5.4 s, replay check identical, run stored;
  - the four bench decks, 40 games: 37 played, 1 stall, aggro 35.1% (13/37), replay check identical, run stored.
- **Crash, same as Python:** 3 of the 40 bench games crashed with "unreasonable number of hybrid symbols in a cost".
  - It is an attack cost with one `{W/P}` per attacker, against a Goblin swarm. Python's bench hits it too (1 of 20 games).
  - The guard is in `find_payment` (mana/), which searches every way to pay hybrid and Phyrexian symbols.
  - A fix would pay Phyrexian symbols greedily instead of searching. That changes behaviour against Python, so it waits until after the port unless the auditor says otherwise.
- **Web session, for the harness table:** "Commander cast 0 of N" for opponents is not an engine bug. The observer tracks only the hero's commander, as Python's does (observer.py:139). The column could show "-" for opponents.
- **Note for anyone running the page locally:** `cache/ir/v1` was a stale IR v1 export. The server reads `ir/v2`; I exported it (`python -m mtgfish.tools.ir_export export`, 38,800 cards, 93 s).

## For the rules session (6441531, 9b48947)

- **Second fixture batch is in** (6441531): all 21 you asked for, 53 cards in all.
  - For the vague entries I used the cards your Python tests use: Mentor of the Meek, Sword of Fire and Ice, and Wort, the Raidmother (conspire).
  - The undying granter is Mikaeus, the Unhallowed.
- **`cardCatalog(game)`** is in digital_mechanics.ts (9b48947). turn.ts's CR 727 restart uses it now: a one-line change in your file, with tests green.

- **Soul-Guide Lantern and Ulamog, the Ceaseless Hunger** are in (d59284f); both read in full. That makes 55 fixtures.
- **Granted persist and modular:** I looked for a real card that grants persist, to stand in for Python's keyword builder.
  - Cauldron of Souls is the obvious one, but its ability doesn't parse yet: "Choose any number of target creatures" isn't read. So it's no use as a fixture, and I left it out.
  - I added that to the card-reading fixes for after the port.
  - Until then those two cases stay in Python, as you said.

## F63, the hybrid-cost crash: fixed (20610f4)

- `findPayment` (mana/payment.ts) no longer throws past 12 Phyrexian or `{2/W}` symbols.
  - The symbols past `MAX_SEARCHED_CHOICES` (12) are settled greedily first: mana of a kind the symbol accepts while one is spare, else 2 life (Phyrexian) or 2 generic (`{2/W}`).
  - The first 12 are searched exactly as before, so a cost at or under the limit pays as it did.
  - A payment that uses the fallback never pays life down to 0. That's a policy, not CR 119.4.
  - If even that can't pay, the cost is unpayable, and `affordable()` drops attackers until it can.
  - Marked `// KNOWN: F63 (Python raises here)`.
- Tests: 3 in mana.test.ts, and `test/rules/f63_attack_tax.test.ts` on real cards (Norn's Annex added to the fixtures). 16 Bears and 2 Plains attack in full at 40 life, and 11 of them at 20.
- **The three browser games that crashed** (bench seed 0, games 20, 30, 32): all three crash with the old payment code and finish with the fix (winners P2, P2 and P0, on life).
- **Bench, 40 games, vitest:** aggro against combo/control/stax, seed 0, turn cap 25.
  - 0 crashes.
  - Aggro won 14/40 (35%); 1 stall (2.5%).
  - Wins by seat: 14 / 1 / 16 / 8, plus 1 draw.
  - Endings: LIFE 34, COMMANDER_DAMAGE 4, STALL_OUT 1, DECKING 1. 50.6 turns on average.
  - For comparison: Python got 31.6% with 10.5% stalls (20 games), and the browser 35.1% with 2.7% stalls.

## Results and replay screens read browser runs (done)

- **Engine** (43fe6e7): `sim/replay_view.ts` ports `sim/replay.py`, and `sim/board.ts` ports `sim/board.py` (the film).
  - `replayGame(setup, index, seed)` plays a game again with the log and the film on.
  - The replayed game is the run's game: its digest is tested equal.
- **Payloads** (d565832): `sim/payload.ts` builds the Replay screen's JSON, field for field as Python's Bridge sends it.
  - `replayPayload` matches `replay`, and `boardPayload` matches `replay_board` (short arrays with Python's bit flags).
  - Parse status comes from the IR.
  - The Results payload is the web session's `webapp/src/results.ts` `reportPayload` (1a7917e). My duplicate `resultsPayload` is removed: it lacked their metric-name mapping, so per-turn charts missed `mana_available` and similar metrics.
- **Server** (7fa0361): `GET /api/browser-runs/{id}` returns the run whole, with `setup` (decks and current IR). If a card has changed since the run, `setup` is null and `unverifiable` says which card. Owner only.
- **Site bundle** (07b9098): `webapp/src/site`, built by `npm run build:site` into `mtgfish/ui/web/engine/` (generated, gitignored).
  - The Results report is built from the records.
  - Replays run in a Web Worker and flag a digest mismatch.
  - The Dockerfile's node stage builds the bundle. Its steps pass from a clean copy, but the image itself hasn't been built: there's no Docker daemon here.
- **Site** (9897e43): History lists browser runs.
  - Open shows the run on the Results screen.
  - While a browser run is open, `call()` in app.js sends `report`, `replay` and `replay_board` to `browser_runs.js`.
  - Starting a simulation, or opening a server run, closes it.
- **Checked in Chromium** against the local server: a 12-game browser run was uploaded, listed in History and opened.
  - Results: 50% win rate, charts, inert cards.
  - Game 2 replayed in about 1 s, with the turn list, the log, and boards showing attackers, blockers, damage and the commander.
- **Still to do: card art in browser replays.** `board_cards` carries `art: ""`, so cards draw as placeholders.
  - Python looks the art up in its card database, tokens by name.
  - Proposal: the server adds image URLs per card name to `GET /api/browser-runs/{id}`, tokens included, by the same lookup `Bridge._board_cards` uses.

## Token swarms that never end (auditor's top priority, 15 Nov)

**The diagnosis didn't hold for the TS bot.** It's aggressive by default (as in Python), so it already attacks with every creature that has power. The toughness rule only applies with `aggressive: false`. I traced all 14 runaways on the 200-game bench (runSeed 20261114) instead.

**Root cause in 5 of 14: an engine bug, fixed (be2499a).**
- `durations.ts` asked whether `game.turn > entry.createdTurn`, but standing restrictions and permissions store `created_turn` (restrictions.ts). So "until your next turn" never ended.
- Teferi's Protection's "your life total can't change" lasted for the rest of the game. In game 155, 239 unblocked goblins dealt 1 each to a player on 3 life, who stayed on 3.
- Games 40, 107, 117, 155 and 184 now end with a LIFE win.
- **Rules session:** the fix is in your file (a one-line read of `created_turn`, with a test in actions.test.ts). Please review.

**The bot (1cb2be4):** it now passes over a player whose combat damage keeps being prevented (Solitary Confinement) while another opponent can be hurt.
- The auditor's alpha-strike and swarm rules would change nothing (it already attacks all-out).
- I tried the lookahead size budget and dropped it: it slowed the biggest games (game 9: 21 s to 31 s).

**Bench, 200 games** (seat 0 aggro, 1 combo, 2 control, 3 stax):

| | LIFE | CMD | RUNAWAY | DECK | STALL | wins 0/1/2/3/none | median | p90 | max |
|---|---|---|---|---|---|---|---|---|---|
| before | 129 | 47 | 14 | 5 | 5 | 51/11/81/38/19 | 1.21 s | 3.08 s | 19.9 s |
| be2499a | 163 | 21 | 12 | 2 | 2 | 74/15/53/44/14 | 1.10 s | 2.77 s | 21.1 s |
| be2499a + 1cb2be4 | 163 | 22 | 11 | 2 | 2 | 73/15/54/45/13 | 0.97 s | 2.55 s | 18.9 s |

- Control's 81 wins were inflated by the stale protection.
- Games without a winner fall from 19 to 13.

**The 11 runaways left are locks the rules allow:**
- Solitary Confinement on the last player standing (games 27, 77).
- An attack tax against a Krenko board that land destruction left with 4 to 16 lands: Propaganda (9, 33, 102), Ghostly Prison (173), Sphere of Safety (70), Windborn Muse (187). The stax player sits on 20 to 476 life.
- Games 55, 119 and 123 are new paths after the fix, not yet examined.
- Only small improvements are left: keeping mana back for an attack tax (game 33 taps out before combat), or no longer making tokens that can't attack (which would turn a runaway into a stall).

**One requested test not added:** "a 1/1 against a lone 2/2 with no lethal stays home". The default bot attacks there, as Python's does. Making it cautious would change every normal game; I'd want the user's call first.

## Triage of the parser-owned findings (15 Nov)

Every finding checked against the IR as exported now, on the TS engine's fixtures: `engine/test/rules/audit_triage_parser_real.test.ts`.
- Passing tests are readings that match the card.
- `it.fails` with a `KNOWN: Fxx` marker are still open.

(The rules session's file of the same kind is `audit_triage_real.test.ts`; mine has a parser-specific name.)

**Fixed (the reading matches the card):**
- F25 Ertai's Trickery: stays unread, rather than countering on its own kicker.
- F31 Counterspell: targets spells only.
- F33 Turn to Frog: until end of turn, on the one target.
- F43 Sly Instigator: one target, then goad it.
- F46 Harnesser of Storms: stays unread.
- F51 Ulamog, the Ceaseless Hunger: two targets.
- F58 Replication Technique: a token copy of the one target.
- F4 Crew: total power.
- F34 Embalm: unread, and now flagged as such (below).
- F49:
  - Llanowar Wastes: damage on either colour.
  - Chainwhip Cyclops, Phase Dolphin, Insufferable Balladeer: targeted.
  - Jaya Ballard: up to three, then that many.
  - Echo, outlast, cumulative upkeep (pays per age counter), detain (up to two, until your next turn).
- F59 Soul-Guide Lantern: today's Oracle text says "exile target card from **a** graveyard", so the finding's premise ("an opponent's") is out of date. The reading matches the card.
- F28 Overload: fixed in the engine from the keyword (rules b28f152).

**Still open** (each an `it.fails`):
- **F37 Toxic Deluge:** "pay X life" is read as an effect, not an additional cost. BLOCK.
- **F49:**
  - Fists of Flame: a flat +1 on the board. The count sits on a SEQUENCE around the pump, which nothing repeats.
  - Chandra, Spark Hunter's +2: "If you do, draw" is read as a third mode.
  - Arcade Cabinet: the "up to four target creatures" aren't targeted.
  - Relentless Pursuit: "a creature card and/or a land card" is read as one card.
  - Borrowed Knowledge: "target opponent's hand" is read as every opponent's.
- **F3 Sudden Impact, and Viseling (F49):** the hand count has no owner, so it counts every hand.
- **F17 Tannuk:** "Artifact cards and red creature cards" applies "red" to both.
- **New:** Turn to Frog's "blue" Frog loses the colour. Architects of Will has the target player look, not you (only the order of the cards differs).

**Found while triaging, and fixed: `fully_read` was too generous (codec.py).** A face was "fully read" whenever there were no parse failures, even with an UNPARSED placeholder inside an ability (Sacred Cat's embalm, Ulamog's Crusher's annihilator, Dusk // Dawn's aftermath, Rest in Peace's replacement).
- It now uses the same test as the coverage report.
- This affects only the exported flag, the bracket's "unread" tag and the site's inert-card list. The census already counted these cards as unread.

**F37, parser half: fixed (304e291).** An additional cost of X ("pay X life", "discard X cards", "sacrifice X creatures", "exile X cards from your graveyard") is read as `Ability.additional_cost` with an X amount, not as an effect.
- Census: +3, -0, 18 changed, all this family.
- **For the rules session, the engine half:**
  - `componentAmount` (cost_checks.ts:56) evaluates a cost's amount without a source, so a spell's "pay X life" evaluates X as 0. Passing the spell as the source would read its `xValue`.
  - Legality offers no X for a spell whose only X is in an additional cost (CR 601.2b). `asksForX` covers activated costs only.
  - Test: "cast with X = 3" in audit_triage_parser_real.test.ts (it.fails, KNOWN: F37 engine half).

**F49 fixes since** (each with a census against the one before: 0 gained, 0 lost; every changed reading is the same bug corrected):
- Fists of Flame (0f485f7): "+1/+0 for each card drawn" scales the pump. 3 changed.
- Arcade Cabinet (38fe845): "each of up to N target" is targeted. 32 changed.
- Chandra, Spark Hunter (c5e6814): only the "or"-joined effects are the choice, and "you may" covers it. 40 changed.
- Relentless Pursuit (e29d9f5): "a X and/or a Y" is up to one of each. +1, -4 (other "and/or" shapes are now unread rather than misread), 4 changed.
- F17 Tannuk (a1bab7f): a qualifier on one item of a list stays on that item, and a leading one goes with the first item. +2, 14 changed.
- Turn to Frog's colour (60f989d): "becomes a <colour> <type>" sets the colour. 15 changed.

**F3, "that player's hand" / "their hand": needs both sides.** Today it's a COUNT of hand cards with no owner, so it counts every hand. The right IR is CARDS_IN_HAND for the referred player (TARGET_PLAYER, TRIGGER_PLAYER, THAT_PLAYER). But `evaluate` resolves a value's players without the resolution's context, so the referred player isn't reachable.
- Proposal: `EvaluateOptions` gains a `resolvePlayers(spec)` callback (values.ts is mine). resolve.ts's `evaluateContext` passes one built on `playersOf` / `referredPlayers` (rules).
- The parser then emits CARDS_IN_HAND for the referred player. **Rules session: does that suit you?**

**Seen, pre-existing:** Jaws, Relentless Predator's "is sacrificed or destroyed, this deals 1 damage" is split inside its trigger, so "destroy this permanent" becomes an option. To fix.

**Cards that don't read yet:**
- Cauldron of Souls and Cauldron Haze now read (320500d): "Choose ... target ... . Each of those creatures ..." becomes one targeted sentence. Board test: the Bears come back with a -1/-1 counter. This also resolves the card-reading gap logged above.
- Tireless Provisioner and Ant-Man's Army read (de8b72e): "create a Food token or a Treasure token" is a choice of one.
- Command Beacon reads (fb9edb1): "your commander" is the commander you own, and "from the command zone" after the destination says where it is. That also reads Jeweled Lotus, Skyfire Phoenix, Geode Golem, Road of Return and six more. Census: +11, 0 lost, 36 changed.
  - Two engine faults showed up, both fixed in the same commit. **Rules session, please look:**
    - `replacement.ts` `commanderZoneReplacement`: a commander leaving the command zone for a hand was put back (CR 903.9b offered "instead" and the default took it). It now skips the choice when the commander is already in the command zone.
    - `pool.ts` `restrictionPermits`: a mana restriction to "your commander" paid for any spell, because only types were checked. `PaymentContext` now carries `commander`. Still unchecked: `of_chosen_type` (Pillar of Origins pays for any creature spell).
  - Graveyard triggers were dead: a triggered ability that returns "this card from your graveyard" worked only on the battlefield. Bloodghast, Squee, Punishing Fire, Akoum Firebird and about 30 more never fired. Now such a trigger works in the graveyard (CR 113.6m), except Aura triggers on what they enchant, which look back from the battlefield. Those are the 36 changed readings.
- "Discards ... at random" reads (bb628cf): Hymn to Tourach, Mind Twist, Gamble, Hypnotic Specter and 30 more. Census: +34, -4.
  - **Rules session:** `resolve.ts`'s DISCARD executor now picks with `game.rng` when the effect carries "at random", instead of asking the agent. Only these cards draw from the generator, so other games' digests are unchanged.
  - The 4 lost: "unless any player pays" (Cleansing, Icy Prison, Nakaya Shade, Soul Strings) was read as each player asked in turn. The first payment should stop it, and the engine charged every willing player. The IR can't say "any one player", so they're unread until it can. An IR addition (a payer scope "any player, first to pay") would bring them back with Aether Rift; say if you want it.
- Boseiju, Who Endures reads (65c6600): "a land card with a basic land type" is any of the five (CR 305.6). Four more cards came with it. Census: +5.
- The Zeniths and Beacons read, and Chaos Warp works (e1dc35c). Census: +8, 7 changed.
  - Chaos Warp, Oblation and Ominous Cemetery were misread: the target was dropped, so nothing moved. Now the target moves.
  - **Rules session:** `resolve.ts`'s MOVE_ZONE now shuffles the owner's library when the move says "shuffle". Before, "shuffles it into their library" put the card on the bottom. Not covered: CR 701.24c shuffles the library even when the card wasn't where it was expected; today a move that finds nothing shuffles nothing.
- "If <condition>, <effect> instead" reads where it is safe (406da60): Field Research, Scute Swarm, Leafkin Druid and 10 more; also Sword of Feast and Famine. Census: +14.
  - Not yet: the ~30 where the replacement says "that creature" or "it" (Explosive Growth, Electrostatic Bolt, Elder Cathar), or has its own target (Tear Asunder). They need the first effect's target bound before the branch is chosen, so the condition can ask about it. That's an IR question (a "choose the target, then branch" shape); say if you want it.
- "As long as this creature is equipped / enchanted" reads (b2ce87b): Kor Duelist, Fireblade Charger, Gate Hound and 16 more. Census: +19.
- Next: the most-played unread abilities by deck usage. In order of play rank: Path of Ancestry, Myriad Landscape, Arcane Denial, Urza's Saga, Mana Drain, Victimize, Windfall, Idol of Oblivion, Force of Negation... (each needs a new shape: a mana-spent trigger, "share a land type", a delayed draw for the countered spell's controller, a granted Saga ability, mana from what was countered) Borrowed Knowledge waits with F3 for the evaluate-context change.

## For the rules session: your 200-game bench

- **Game 63's hang: fixed (b52996d).** `findPayment` now tries one mask per class of interchangeable choice symbols (same kind, colours and generic): the class's smallest, in the original popcount-then-value order. That gives the same payment as the full walk.
  - Fifteen `{W/P}` are 16 branches, not 4096.
  - A necessary check runs first: with too little mana and life in total, it returns null without searching.
  - Checked against the previous `findPayment` on 20,000 random costs and pools (9,694 of them payable): identical results.
  - Game 63 now finishes in 3.4 s (commander damage, turn 54); games 55 and 57 take about 1 s.
- **Token swarms: I tried the CR 111.5 batching, measured it, and left it out.**
  - Batching was exact: same digests on 16 bench games.
  - But it gained nothing. With your ce0b202 in, games 33 and 9 reach the same turn in 45 s with or without it (turn 40 with about 3,600 permanents; turn 51).
  - `prohibited` no longer shows in the profile.
- **Where game 33's time goes now** (45 s CPU profile, inclusive):
  - About 60% is `createTokens` -> `emit` -> `collectTriggers` -> `watchers` -> `characteristicsOf` -> `computeBoard`. Each token's TOKEN_CREATED and ENTERS_BATTLEFIELD events collect triggers after an invalidation, so each one recomputes the whole board.
  - About 45% is the bot's lookahead (`chooseAction` -> `project`), which clones and plays out on the same huge board. (The two overlap.)
  - The trigger path is in emit/triggers, which is yours, and Python has the same shape.
  - One exact option: in `watchers`, cache the list of permanents with triggered abilities per characteristics epoch, and have `createTokens` invalidate once per batch rather than once per token. But the per-token invalidation is there for CR 111.5's per-token check, so that's a design call.
  - Lookahead on big boards is the AI's, which is mine now. Capping it would change which actions the bot picks (and the digests), so I haven't touched it. Say if you want a size cap.

## Data paths in mtgfish/ir (the auditor's e1cda56)

- Pulled before touching it. The only other direct read of the repository's `cache/` was `game_changers()`'s fallback to `<repo>/cache/scryfall`. It now reads through `paths.scryfall_cache()` only.
- The other repository paths in `ir_export` are the dev tools' defaults for committed files (`schema/`, `bench/`), not card data.

## For the web session (welcome back)

- I did these while you were archived. I've now seen your 14 Nov plan, and the **replay screen for browser runs** (your Next 2) is done, as above.
  - The frame format is Python's `ReplayFrame`. It needed nothing new from rules: the TS log already has turn, phase, step, player, kind, text, depth and active player.
  - Yours to change as you like.
- **One bundle, not two?** Mine is `mtgfish/ui/web/engine/goldfly-site.js`, from `webapp/src/site`, built by `npm run build:site`. It has the report from stored records and the replay worker.
  - Your switch-over plan puts a runner bundle at `mtgfish/ui/web/browser/`.
  - Folding your runner into `webapp/src/site/entry.ts` (or moving mine into yours) would give the site one engine bundle. Your call.
  - The Dockerfile's node stage already builds it.
- **The seam in app.js:** `call()` asks `BrowserRuns.handles(method)` first (`mtgfish/ui/web/browser_runs.js`). For your Run, setting the current browser run after a run finishes makes Replay work with no other change.
- The site bundle uses your `reportPayload` and `unparsedCards`.
- I'm leaving the website switch-over to you. Card art in browser replays is still to do; it's yours if you want it, or I'll take it.

## Card-reading gaps found during the port (for after it)

- ~~Cauldron of Souls~~: reads since 320500d.

## Next

- Card art in browser replays (above), unless the web session takes it.
- The website switch-over is the web session's again (their plan in status/web.md), and waits for the auditor's go.
- After that, the unticked items are:
  - the two bracket items, Phase 2, parked until the port is done;
  - the deferred rules fixes, "once games can be watched".
- **Board tests: done.** Every Phase 1 item in my section is ticked.
  - Each module came with its tests.
  - For mana/ and match/:
    - stale sources (782379a);
    - history conditions and any_of (e01e481);
    - values this way (323b664);
    - dethrone (e9f38b1);
    - affected values (a804ba2);
    - modal mana (c277632);
    - mana planning on real cards (038ad26);
    - entering and mana, and Command Tower (bf242a9);
    - filter bounds (0b5136a).
  - Owner filters were covered already.
  - What stays in Python is parser readings and Sandbox runs of whole cards, which are tests of the parser or of the Python UI, not of these modules.
- **New for board tests that need real cards:**
  - `ir_export fixtures` exports the cards named in `schema/fixtures.txt` to `schema/fixtures/`, and a Python test keeps them current.
  - `engine/test/rules/real_cards.ts` has `RealBoard` and `REAL_ABILITIES`, which read them as `play()` does.
  - Rules session: you're welcome to use them. Add a name to `fixtures.txt` and regenerate, or tell me and I will.
- **wasUndeterminable** is installed (0d33632), and your `numbers.test.ts` case is `it` now.
- Then the PENDING(rules) records in `match/` as you add them, and the IR side of anything the port turns up. Card-reading fixes wait until after the port.

## Questions for the auditor

- None. The two bracket questions (combo list source and licence, and the thresholds) are parked for the user at Phase 2, as you said.
