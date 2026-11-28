# Data refresh: Scryfall snapshot 2026-11-15

One-off data-refresh agent, 2026-11-16. Scryfall's bulk dumps were refreshed from
the 2026-09-16 snapshot to the one Scryfall published on **2026-11-15T22:02Z**.

## What was done

1. Built the card database from the old dumps (`fetch_scryfall --offline`), exported the
   IR (`ir_export export`) and recorded `parse_report` / `play_rate_report` as the baseline.
2. Deleted `cache/scryfall/catalog-*.json` and ran `python -m mtgfish.tools.fetch_scryfall`.
   The catalogs have to be deleted first: `ScryfallClient.catalog()` returns the cached
   file forever and never refetches it.
3. `fetch_scryfall` downloaded the new dumps and pruned the old ones; the three
   superseded `*-20260916T…jsonl.gz` files are `git rm`'d, so the image carries one snapshot.
   `cache/pool.json` updated (39,295 cards, 79,706 rulings, 240,247 oracle tags).
4. `bootstrap.seed_card_database()` picks the dumps via `ScryfallClient.cached_bulk()`,
   which takes the lexically last `oracle_cards-*.jsonl.gz`. Only one is left now, and
   `pool_is_stale()` compares the filename the database was built from, so existing
   checkouts and servers rebuild on their next start.
5. Exported the IR again and diffed it against the baseline; ran a census of the new pool.
6. Regenerated `schema/fixtures` and `schema/samples` (`ir_export fixtures` / `samples`).
   Every fixture changed only because `card_hash` covers `edhrec_rank`.
7. Full test suite, then a Docker build.

## Before → after

| | before (2026-09-16) | after (2026-11-15) |
|---|---|---|
| cards in the DB (all) | 38,802 | 39,295 |
| IR export files | 38,800 | 39,293 |
| parse_report: Commander-legal cards | 31,830 | 32,643 |
| parse_report: fully read | 17,004 (54.0%) | 17,359 (53.8%) |
| parse_report: abilities read | 42,845 / 60,121 (71.3%) | 43,889 / 61,731 (71.1%) |
| failures: plain / triggered / activated | 7,002 / 4,991 / 2,205 | 7,241 / 5,150 / 2,273 |
| play rate, top 100 | 96.0% | 96.0% |
| top 250 | 93.6% | 93.6% |
| top 500 | 89.0% | 88.6% |
| top 1000 | 81.8% | 82.1% |
| top 2500 | 65.9% | 66.2% |
| top 5000 | 56.5% | 56.5% |
| top 10000 | 51.3% | 51.3% |

Census of the whole new pool (`census --pool all`): 35,360 cards, 17,713 of 34,698
fully read (51.05%), abilities 45,832 / 66,813 (68.60%). (No "before" census was taken; the
table above is the before/after comparison.)

The play-rate shifts come from re-ranking (EDHREC ranks move with every snapshot), not
from parser changes: no card that was already in the pool changed IR except through errata
(below).

## New cards and sets

**709 new oracle ids** (448 Commander-legal); **216 removed**.

| set | new | name |
|---|---|---|
| trk | 219 | Star Trek |
| trc | 135 | Star Trek Commander |
| fra | 76 | Reality Fracture |
| afra | 50 | Reality Fracture Art Series |
| atrk | 47 | Star Trek Art Series |
| ahob | 46 | The Hobbit Art Series |
| yfra | 31 | Alchemy: Reality Fracture |
| frc | 17 | Reality Fracture Commander |
| unk | 15 | Unknown Event |
| atrc / ahoc | 12 / 12 | Star Trek Commander / Hobbit Eternal Art Series |
| tfra / ttrk | 10 / 10 | Reality Fracture / Star Trek tokens |
| nau, mdd | 7, 7 | Nauctis: The Sunken Realm; Marvel: Darkhold Destiny |
| other | 13 | tmdd 4, slc 2, ttrc 2, amkm, tfrc, mdc, tnau, pspl, sld, pf26 1 each |

A further **365 cards already in the old snapshot became Commander-legal** (released):
fra 192, trk 75, mbc 60, trc 37, frc 1. None stopped being legal.

**How many are fully read:**
- new Commander-legal cards: **192 of 448 (42.9%)**;
- cards that became Commander-legal: 164 of 365 (44.9%);
- both together, what entered the Commander pool: **356 of 813 (43.8%)**, against the
  pool's 53.8%.

**Removed:** the 216 are all Alchemy rebalanced `A-` cards (A-Blood Artist, A-The One Ring,
A-Teferi, Time Raveler, ...). Scryfall's `oracle_cards` dump no longer carries them as
separate oracle cards (436 `A-` lines before, 1 now: `A-Town`). None was Commander-legal.

**Other changes to existing cards** (IR diff ignoring `card_hash`/`edhrec_rank`): 1,383 cards.
- 859: `scryfall_uri` only.
- ~365: `commander_legal` flipped to true (above).
- 72 changelings and other "all creature types" cards: the IR's type list grew with the
  27 new creature types.
- **92 cards with changed Oracle text (errata)**, e.g. Dragon's Approach, Ambition's Cost,
  Way of the Mentor / Wildspeaker / Necromancer / Healer / Mind Sculptor / Paradox (now
  "empower Jace N"), Josu Vess, Sarkhan Dragon Ascendant, Vesuvan Mist, Phyrexian Arena,
  God-Eternal Bontu, Geth, Gallia, Akroma Angel of Fury, Everyone's an Impostor (renamed).
  Brackets changed for Josu Vess, Sylvan Primordial, Gallia, In the Pale Moonlight.

## Top 20 unread wordings in the new cards

From the census of the 813 cards that entered the Commander pool (new + newly legal),
grouped by where the grammar stopped:

| # | stop point | example |
|---|---|---|
| 17 | inert (UNPARSED node / unreadable condition) | Twinned Vision: "If this spell wasn't cast from your hand, draw two cards instead." |
| 17 | `This creature enters prepared` | Void Extrapolator // Omit Variables |
| 14 | `Strange new worlds -` (new ability word) | The Destruction of Praxis |
| 13 | a keyword argument that cannot be read whole (`Seek out -` ability word) | Beverly Crusher: "Seek out - Whenever you draw your second card each turn, ..." |
| 11 | `this can be your commander` | Jace, Multiverse Architect |
| 10 | `from the top of` (reveal until) | Janice Rand, Essential Aide |
| 10 | `Planeswalkers you control have "[+2]: ..."` | Sanctum Lurker |
| 8 | `where X is the` (cost reduction by X) | Warp Speed |
| 7 | `Empower Jace 2.` (new keyword action) | Hexhaven Battalion |
| 7 | `Whenever you face a dilemma` | Gabriel Lorca, Mirror Menace |
| 7 | `Until end of turn, whenever ...` | Right of Vengeance |
| 7 | `empower Jace 5.` | Way of the Mentor |
| 7 | `A deck with this commander can have ...` | Grizzlegom, Hurloon Hero |
| 6 | `Whenever you attack while ...` | Kirk, Commanding Officer |
| 6 | `8+ \| Flying` (Spacecraft station thresholds) | D-7 Battle Cruiser |
| 5 | `Whenever you scry or surveil` | Saheeli, Consul of Oversight |
| 5 | `6+ \| Flying` | U.S.S. Cerritos, Following Up |
| 5 | `When this Spacecraft enters` | Borg Scout Sphere |
| 5 | `empower Jace 2.` (lower case) | Arcane Amphisbaena |
| 4 | `Beam me up {2}{W}` (new keyword) | Pure Energy |

Over all 709 new cards: Doctor Strange-style "Activate only if you've cast a noncreature
spell this turn", "As long as this is in the command zone or on the battlefield",
"Once during each of your turns", "unless your opponents control eight or more lands",
and "types among non-Borg creatures" also recur.

## New keywords (tests/rules/test_keywords.py: 3 failures, expected)

New entries in Scryfall's catalogs:
- **keyword abilities not in the registry:** `Affinitycycling`, `Beam me up`, `Halflingcycling`
  (`Storied` is also new in the catalog but already registered);
- **keyword actions not in the registry:** `Empower Jace` (`Recruit` is new but already registered);
- **ability words not in the registry:** `Actualize`, `Battlefront`, `Federation`, `Seek out`,
  `Strange new worlds`;
- new subtypes: 27 creature types (Star Trek species: Android, Bajoran, Betazoid, Cardassian,
  Ferengi, Jem'Hadar, Romulan, Tribble, Trill, ... plus Mindless-One, Kwejian), artifact
  type `Communicator`.

Not fixed here: these go in `mtgfish/rules/cr700_additional_rules/keywords.py`, which the
rules agent owns.

## Comprehensive Rules

**Not checked.** `mtgfish/data/comprehensive_rules.download()` fetches from
`magic.wizards.com`, which this environment's network policy blocks (proxy answers 403).
`tests/rules/test_citations.py` passes against the committed `cache/comprehensive_rules.txt`,
but that is the old release, so it says nothing about drift. A session that can reach
magic.wizards.com should run `python -m mtgfish.tools.rules diff <new CR .txt>` and the
citation tests: Star Trek / Reality Fracture likely came with a CR update (Beam me up,
Empower, Spacecraft-related rules).

## Tests

`python -m pytest -n 4`: 3418 passed, 1 skipped, 5 xfailed; failures after regenerating
fixtures/samples:
- the 3 keyword-registry tests above (new keywords, expected);
- **`tests/parser/test_explain.py::test_every_effect_opcode_the_parser_emits_has_a_phrasing`**:
  11 unphrased opcodes against a ceiling of 10. Not a regression in the parser: the test
  samples the first 4,000 Commander-legal cards in DB order, and the new card
  **Imperial Hullbreachers** lands in that window and emits `REPEAT_PER_PAYMENT`, which
  has no phrasing in `mtgfish/parser/explain.py`. Fix: add a phrasing for
  `REPEAT_PER_PAYMENT` (parser agent). I didn't raise the ceiling, because the test
  exists to stop that number growing.

The four `tests/web/test_ir.py` failures and `tests/ir/test_fixtures_export.py` were all
`card_hash` drift and pass after `ir_export fixtures` / `samples`.

## Docker

The image builds and seeds from the new dump only (39,293 IR files; `/app/data/scryfall`
holds only the 2026-11-15 dumps). The committed Dockerfile can't build *in this sandbox*
because the sandbox's TLS-intercepting proxy CA isn't trusted inside the build (pip:
CERTIFICATE_VERIFY_FAILED). It built from a scratch copy that mounts the proxy CA as a build
secret for npm/pip/apt. That copy isn't committed and has no effect on the live build.

## For other agents

- **Rules agent:** register `Affinitycycling`, `Halflingcycling` (cycling variants),
  `Beam me up` (keyword ability), `Empower Jace` (keyword action), and the ability words
  `Actualize`, `Battlefront`, `Federation`, `Seek out`, `Strange new worlds`. Check the CR
  once it can be fetched.
- **Parser agent:** phrasing for `REPEAT_PER_PAYMENT` (failing test). The biggest new
  wordings: ability-word prefixes `Strange new worlds -` / `Seek out -` (should be free once
  registered), "enters prepared", "empower Jace N", "face a dilemma", "this can be your
  commander", "A deck with this commander can have ...", Spacecraft station thresholds
  (`N+ | ...`) and "When this Spacecraft enters", "Whenever you attack while ...",
  "Whenever you scry or surveil", static grants of loyalty abilities ("Planeswalkers you
  control have "[+2]: ...""), and "Beam me up {cost}".
- **Infra:** `ScryfallClient.catalog()` never refreshes a cached catalog; a refresh
  has to delete `cache/scryfall/catalog-*.json` by hand. `fetch_scryfall` could clear
  them when the bulk index's `updated_at` changes.
