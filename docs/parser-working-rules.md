# Working on the parser

The rules every change to `mtgfish/parser/` is held to, and the loop for
checking a change against them. Written for whoever picks up the next batch of
unread cards - person or agent.

## The pool

The parser is measured over **every card legal in at least one format**
(`format_legal = 1` in the card database; `census --pool format`, the default).
Commander is one of those formats, not the target. Cards legal nowhere - Un-set
joke cards, playtest cards, test cards - are left out of the measurement. They
may parse or not; nothing is owed on them.

Digital-only cards (Alchemy "A-" rebalances, Arena-only mechanics like
*perpetually*, *seek*, *conjure*) are legal in Historic, Alchemy, Timeless and
Brawl, so they are in the pool.

## The rules

1. **An honest "I don't know" beats a guess.** If the grammar cannot say what a
   sentence means, the ability stays unread (`UNPARSED`) and the card is
   reported as not understood. That is a correct outcome. A parse that runs and
   does something slightly different from the card is the worst outcome there
   is: it looks like coverage, it moves the simulation's numbers, and nothing
   downstream can tell.

2. **Full consumption.** Every token of an ability is accounted for in the IR,
   or the whole ability is unread. There is no partial credit.

3. **No swallowing.** Consuming a word is a claim that its meaning is in the
   IR. A rule that accepts words and drops them - "during", "if able",
   "this turn", "for each", a trailing "that player controls" - converts an
   unread card into a wrong one. If a word changes what happens, the IR has to
   change with it. If you are tempted to `accept()` something and not use it,
   write down in a comment exactly why it carries no meaning (CR citation), or
   do not accept it.

4. **No invention.** The parser only emits opcodes the engine executes. When a
   card needs behaviour the engine lacks, either implement it in the rules
   engine properly - with its CR number, an executor, and a test on a real
   board - or leave the card unread. Never approximate one effect with
   another ("each opponent" is not "target opponent"; "up to one" is not
   "one"; "exile until" is not "exile"; "any number of" is not "all").

5. **Oracle text only.** Card behaviour comes from the oracle text and nothing
   else. No per-card special cases keyed on a card's name. Scryfall's tags are
   a *cross-check* (`parse_report --crosscheck`): they may tell you that a card
   which parsed is probably wrong, and you then go and read the card. They are
   never an input to parsing. That keeps the parser updatable by re-reading
   oracle text, and keeps community data from being able to change what a
   card does.

6. **Generalise from templates, not cards.** Oracle text is templated. A rule
   should read a construct wherever it appears ("[player] may pay [cost]. If
   [they] do/don't, ..."), not the one card that prompted it.

## The loop

```bash
# once: the card database (seconds, no network)
python -m mtgfish.tools.fetch_scryfall --offline

# before you start: a baseline census
python -m mtgfish.tools.census --out /tmp/before.jsonl.gz

# the work queue: failures clustered by where the parse gave up
python -m mtgfish.tools.census --load /tmp/before.jsonl.gz --clusters 80 --words 3

# every failing ability matching a pattern
python -m mtgfish.tools.census --load /tmp/before.jsonl.gz --grep "would die"

# one card, with the round-trip explanation
python -m mtgfish.tools.parse_report --card "Rhystic Study"

# after a change: what it gained, lost, and changed the meaning of
python -m mtgfish.tools.census --baseline /tmp/before.jsonl.gz --out /tmp/after.jsonl.gz
```

The census diff has three sections, and a change is judged on all three:

* **gained** - cards now fully read. Spot-check them: read the round-trip
  (`parse_report --card`) beside the oracle text for a sample, and every one
  whose text contains a word your rule consumed but did not obviously use.
* **LOST** - cards that were read and now are not. Must be zero, or explained.
* **CHANGED** - cards read before and after, to a different IR. Every one must
  be a *correction* you can state ("X was read as Y; it means Z"). An
  unexplained change of meaning is a regression even when coverage went up.

Then run the tests: `python -m pytest tests/parser tests/rules -q -n 4`.

## Where things go

| Construct | Module |
|---|---|
| splitting a text box into abilities, modal blocks, sagas, levels | `parser/split.py` |
| "At/When/Whenever ..." trigger events | `parser/triggers.py` |
| noun phrases, filters, players, values, counts | `parser/nouns.py` |
| effect sentences (verbs), conditions, tails | `parser/clauses.py` |
| costs of activated abilities, additional costs | `parser/costs.py` |
| keyword abilities and their builders | `rules/cr700_additional_rules/keywords.py`, `cr702_keyword_impl.py` |
| effect opcodes | `rules/cr600_spells_and_abilities/effects.py` |
| executors | `rules/cr600_spells_and_abilities/resolve.py` (`EXECUTORS`) |

New `EffectKind` members need a unique number (`test_no_two_opcodes_share_a_number`)
and an executor (`test_no_invention.py`).
