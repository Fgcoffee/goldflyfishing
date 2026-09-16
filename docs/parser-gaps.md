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
