# Why the parse rate is what it is, and what is still missing

Measured against 31,830 commander-legal cards / 60,571 abilities.

```
Cards fully read   11,242   35.7%
Abilities read     34,984   57.8%
Failing abilities  25,499
```

The gap between those two numbers is the first thing to understand. **More than
half of all abilities parse, but only about a third of cards do**, because a
card counts as read only when *every* ability on it is read. A three-ability
card at a 58% per-ability rate lands near 19%. Most failing cards are one
clause away, not hopeless.

## Before you special-case a card

Two seams exist precisely so that card text can beat a rule without any
per-card code, and reaching for one is almost always right:

- **`docs/rule-overrides.md`** — cards that switch a *rule of the game* off
  ("creatures don't suffer summoning sickness", "ignore the legend rule").
  Emit one `SUSPEND_RULE` effect naming the CR number.
- **`rules/cr500_turn_structure/restrictions.py`** — cards that forbid or
  permit an *act* ("can't attack unless", "may cast from your graveyard").
  The `Act` list is deliberately exhaustive; add to it rather than around it.

If a card needs a rule that is not in either list, that is a gap to fill in
the rules engine, not a reason to name the card in the parser.

## The failures are a long tail, not one bug

Classified by the construct sitting exactly where the parse gives up:

| what is at the give-up point | count | share |
|---|---:|---:|
| noun phrase / determiner | 7,206 | 28.3% |
| subordinate clause (`if` / `unless` / `as long as` / `when`) | 5,605 | 22.0% |
| unclassified | 4,008 | 15.7% |
| prepositional or adverbial tail | 3,518 | 13.8% |
| verb phrase | 3,135 | 12.3% |
| cost, or the ability's overall shape | 1,298 | 5.1% |
| connective (`and` / `or` / `then`) | 712 | 2.8% |
| quoted ability | 17 | 0.1% |

The most common single opening at a failure point occurs about 150 times out of
25,499. The distribution is flat: roughly forty distinct templates, each worth
one to five percent. There is no dominant missing rule.

Two consequences worth stating plainly. **The remaining work is linear** — each
batch is worth one to two percent, and no single insight collapses it. And
**some of it should never be attempted**: a real slice of the tail is cards
whose behaviour the engine has no opcode for (subgames, "outside the game",
planar dice). The parser is forbidden from emitting opcodes without executors,
so those cards *must* fail. They are correctly inert.

## Fixed

### Loyalty costs — 842 abilities

`+1:`, `-3:` and `0:` were unreadable, so every planeswalker in the format
failed — not on its text, which parsed fine, but on its *cost*. The tokenizer
discarded the `+` outright, making `+1:` indistinguishable from `1:`. The
engine's loyalty payment, CR 606.3 sorcery-speed timing and once-per-turn
tracking were all already implemented and simply unreachable. Invisible in a
token-frequency work queue because the failure was not in the text at all.

### Noun phrases

- **Plural creature types.** The subtype registry holds singulars and oracle
  text pluralises freely, so `Goblins you control`, `Elves`, `Attacking Ninjas`
  — every tribal card in the format — failed on its own creature type. English
  is too irregular for one rule, so candidates are tried against the registry.
- **Comma-separated type lists.** `artifact, enchantment, or planeswalker`
  writes "or" once, before the last item only. Requiring a conjunction at every
  step stopped dead at the first comma.
- **Subtype disjunctions.** `Elf or Goblin` was read as a conjunction, which
  matches nothing — nothing is both.
- **Library positions.** `the top card of your library` is a position, not a
  zone (CR 401.2). Added `from_top` to the filter and the matcher, so an effect
  on "the top card" cannot pick any card in the library.
- **Superlatives.** `the greatest mana value among permanents you control`. The
  evaluator could take a maximum over a fixed list of operands but not over a
  filter, where the number of things compared is itself a game-state question.

### Conditions

- **`unless` was misread, not unread** — the dangerous failure mode. Read as a
  sibling clause, "draw a card unless that player pays {1}" became "draw a
  card" *and* something else: Rhystic Study parsed into a **cost increase**, a
  completely different card that still ran. `unless` now modifies the effect
  before it, and "pays {N}" is read as a *cost* rather than as an effect.
  Added `UNLESS_PAYS`, which cannot reuse `OPTIONAL` because the choice belongs
  to a player who is usually not the controller.
- **Trailing `as long as`.** Oracle text puts the condition on either side of
  the effect and means the same thing. Only the leading form was read, and the
  trailing one is the commoner of the two.

### Trigger conditions

The trigger grammar gave up entirely on 1,264 abilities beginning `Whenever`.
Added the missing event phrasings — `is dealt damage`, `becomes the target`,
`becomes blocked`, `is sacrificed`, `phases out` and two dozen more, every one
of which the engine was already emitting and the grammar simply could not ask
for. Also disjunctive subjects: `this creature or another creature you control`
is the union of the source and the others, which is the same filter without the
source exclusion.

### Characteristic-defining abilities

`its power and toughness are each equal to ...` (CR 604.3). These belong in
layer 7a, before every other P/T effect, and function in *every zone* — read as
an ordinary setting effect they would land in 7b and be overwritten by counters.
Includes the split form (`its power is equal to X and its toughness to that
plus 1`).

### Prevention

`prevent all combat damage that would be dealt` with no recipient named, and
`prevent the next N damage` — a shield of fixed size rather than a blanket.

### Mana restrictions — and the bug behind them

`Spend this mana only to cast creature spells` (CR 106.6b). The payment solver
already filtered buckets by asking `permits`, and the `ManaRestriction`
protocol already existed — but **no concrete restriction had ever been
written**, so every "spend only on" rider was dropped and the mana was
general-purpose. Ramp that is meant to be narrow being spendable on anything is
a straightforward overstatement of a deck's speed.

Enforcement had to be added in two places, not one. Payment-time rejection
alone left the spell *offered* as legal and abandoned mid-cast, so a bot would
waste its turn on a play the card never allowed.

### Two silent engine bugs

Neither could ever have surfaced as a parse failure — both cards parsed
completely and cleanly:

- `Add {G}{U}` produced **four** mana. The IR held a count and a *set* of
  colours, and the executor looped the count over every colour. Every dual land
  in the format doubled its output.
- `enters tapped` did nothing. The parser produced the effect and the engine
  had the CR 614.1c replacement machinery; nothing connected them, because a
  permanent that is entering has not yet contributed to the replacement
  registry. Every tapland was a fast land.

Both would have shown up in a simulation only as mana that was slightly too
good — exactly the class of error a goldfishing tool exists to measure and
therefore must not have.

## Read wrongly, not unread

Everything above is about text the parser could not read. This section is about
text it read **incorrectly**, which is a different and worse problem: an
unparsed ability is inert and shows up in the table above, while a mis-parsed
one runs, looks like coverage, and moves the numbers.

None of these appeared in any failure count, because every one of them
consumed its sentence completely and produced a well-formed opcode with a real
executor. They were found by reading the round-trip beside the card.

### Six that were the opposite of the card

- **"Attacks each combat if able"** was emitted as an `Act.ATTACK`
  *prohibition* - the data for "can't attack". A creature that must attack
  every combat became one that could never attack. CR 508.1d makes
  requirements a separate step of the declaration, and `combat._must_attack`
  already enforces it by keyword; the grammar now asks for that keyword.
- **"Can block only creatures with flying"** forbade blocking exactly the
  creatures it is allowed to block. `Restriction.counterpart` forbids on a
  *match*, so a permission has to be written as the complement, and
  `ObjectFilter` has no general negation. One constraint is inverted where it
  can be; anything else fails to parse.
- **A trailing "during ..."** was swallowed by a rule that consumed up to eight
  tokens after the word, so "Creatures you control get +1/+1 during your turn"
  became an unconditional anthem.
- **"Doesn't untap during its controller's *next* untap step"** fell into that
  same swallow and came out as the permanent form: every Sleep and Frost
  Breath in the format tapped a board down for the rest of the game.
- **An unreadable "Activate only ..."** was ignored outright on the
  battlefield. Four shapes are modelled now - once each turn, during your
  turn, during your upkeep, and "only if `<condition>`" through the ordinary
  condition grammar - and anything else fails the whole ability.
- **"Prevent all *combat* damage"** dropped the word, so every Fog also blanked
  a Lightning Bolt; and a shield naming a player narrowed nothing, so a
  one-sided Fog protected the whole table. The shield could always express
  both - it registers its own event kinds and has a `players` field.

### Three that were silently doing nothing

- **Two `EffectKind` members shared a number**, twice over. `IntEnum` makes the
  second an alias, so the executor table kept one entry per *number* and a
  parsed `CHOOSE_QUALITY` was dispatched to the reflexive-trigger executor.
  Guarded now by `test_no_two_opcodes_share_a_number`, which nothing else in
  the suite could have caught.
- **A conditional static ability was dropped whole.** "Gets +1/+1 as long as
  you control a Forest" parses to a CONDITIONAL wrapping a MODIFY_PT, and
  `layers._continuous_parts` walks SEQUENCEs but not CONDITIONALs - so the
  anthem did not apply at all. The condition is lifted onto
  `Ability.static_condition`, which the layer system already checks.
- **`_do_search_library` ignored how the card said the land arrives.** "Put it
  onto the battlefield tapped" had `tapped` in the step list purely to be
  swallowed, so every Rampant Growth produced an untapped land: a full turn of
  mana no deck has.

### And tokens, which were a different card twice over

"Create a 1/1 white Soldier creature token with flying" - the noun reader takes
"with flying" as a filter constraint and files it under `has_keyword`, and
nothing carried it to the `TokenSpec`. Every token printed with an ability was
created vanilla.

"Create a Treasure token" names no card type at all, and the default was
creature, so it made a **0/0 creature** that dies to state-based actions on
arrival - one of the most-played effects in the format producing nothing. The
subtype registry knows what a Treasure is and is asked; where it cannot answer
with one card type, the ability fails.

## Counted as understood while inert

A third category, between the two above: the ability parses, the opcodes are
well formed, and nothing anywhere executes them. These are safe - they do
nothing rather than the wrong thing - but they were being counted as coverage,
which is the one outcome the report exists to prevent.

`ParsedFace.understood` now also rejects an ability whose *condition* could not
be read, which is how a keyword can be inert without leaving a failure behind.
Eight keyword builders are in that state; **Ward** is the one that matters, and
it is worth stating plainly: every Ward creature in the format currently wards
nothing. `keyword_impl._ward` builds the trigger and marks the "unless that
player pays" half `UNPARSED` because the engine has no ward payment. The shape
that would fix it exists - `UNLESS_PAYS`, which Rhystic Study already uses -
and needs a player scope meaning "whoever controls the targeting spell".

### Engine gaps this work found and did not close

Each was reproduced on a board, not inferred from reading:

- **The attack tax does nothing.** Propaganda parses to
  `UNLESS_PAYS(RESTRICTION(can't attack))`, and `restrictions._active` gathers
  only top-level effects whose kind is `RESTRICTION` - so a static
  `UNLESS_PAYS` is skipped entirely and no restriction is ever registered. With
  Propaganda on the battlefield, `prohibited(ATTACK)` returns `None`.
  Separately, the restriction says "can't attack" rather than "can't attack
  *you*": `Act.ATTACK_PLAYER` is declared and never used, and `combat.py` only
  ever asks `prohibited(game, Act.ATTACK, obj=obj)` with no defender. Both
  halves need the engine before the parser can say anything truer.
- **A replacement created by a resolving spell is never registered.** "If a
  creature would die this turn, exile it instead" registers a continuous
  effect, and the replacement layer reads only static abilities. Such a
  replacement is marked `UNPARSED` by the parser now, which matches what it
  does.
- **Requirements have no home.** `Restriction` models prohibitions only, so
  "attacks each combat if able" had to be routed through a keyword. "Blocks
  each combat if able" and "must be blocked if able" have no such keyword and
  do not parse.
- **No duration ends at a player's next untap step**, which is why the "next"
  form of "doesn't untap" declines rather than being read as the permanent one.

## The lesson worth keeping

Most of the above came from the same place: rendering the parsed IR **back into
English from the opcodes** and reading it beside the card. Full consumption
proves a card was read *completely*; the Scryfall cross-check catches some
cards read *wrongly*; only the round-trip shows what the engine actually
believes a specific card does.

The round-trip must never render from `Effect.text`. Every effect carries the
oracle snippet it came from, and echoing that back produces a perfect-looking
paraphrase of a parse that dropped half the sentence.

A related pattern showed up three times and is worth naming: **the engine had
the capability and the grammar could not reach it.** Loyalty payment, the
enters-tapped replacement, the mana-restriction protocol, layer 7a for CDAs —
all built, all tested, none reachable from oracle text. When a category of card
fails wholesale, check whether the engine feature is missing or merely
unwired before writing anything new.
