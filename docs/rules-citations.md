# Citing the Comprehensive Rules

The engine cites the rules about 1,400 times. Every one of those citations used
to be free text: nothing checked that the rule existed, and nothing noticed when
a rules release moved it.

That is not a theoretical problem. When this was first checked, 37 citations
named rule numbers that do not exist, and several more named numbers that had
been *reused for a different rule*:

| The code said | What that number means now | Where the rule actually went |
|---|---|---|
| Speed is CR 702.183 | Tiered | CR 702.179 (Max Speed is 702.178) |
| Search is CR 701.19 | Regenerate | CR 701.23 |
| Untap is CR 701.21 | Sacrifice | CR 701.26 |
| Untapping is CR 502.2 | The day/night check | CR 502.3 |
| Morph face-up is CR 702.36 | Fear | CR 702.37 |

WotC inserts new keywords alphabetically, so a single new keyword shifts every
later `702.x` number. Nothing in a plain comment survives that.

## Looking a rule up

```
python -m mtgfish.tools.rules show 601.2b
python -m mtgfish.tools.rules show 704 --subrules
python -m mtgfish.tools.rules show 502.3 --where     # and who relies on it
```

`--where` lists every file and line that cites the rule, which is the fastest
way to find the code that depends on a rule you are about to change.

## Finding a rule that moved

When a citation looks wrong, search for what the comment *claims*:

```
python -m mtgfish.tools.rules find "Damage dealt to a player"
python -m mtgfish.tools.rules find "inherent triggered abilit"
python -m mtgfish.tools.rules find "untap" --section 701
```

## After a rules release

This is the workflow the whole thing exists for.

1. Drop the new rules text in `cache/comprehensive_rules.txt`.
2. `python -m mtgfish.tools.rules check`

   It reports two things: citations that now dangle, and cited rules whose
   wording has changed since the baseline, printing the old and new text side
   by side with the code locations that depend on them.
3. Read the diff. For each changed rule, check the code still does what the new
   wording says. Fix what has to be fixed.
4. `python -m mtgfish.tools.rules baseline --write`

`mtgfish/data/cr_baseline.json` holds WotC's exact wording for every rule the
engine cites, and it is checked in. So after step 4, `git diff` on that file is
a plain-language record of what changed in the rules and what the engine had to
do about it. That is the comparison that makes a rules update a short list
rather than a re-read of the whole rulebook.

Two tests enforce it: `tests/rules/test_citations.py` fails if any citation
dangles, and fails if a cited rule's wording has drifted from the baseline.

## What this does not catch

A citation that names a rule which exists, but whose text does not support the
comment beside it. `check` cannot see that — the number resolves fine. Only the
baseline diff catches it, and only on the release that changes the text.

`python -m mtgfish.tools.rules audit` helps from the other direction: it lists
rules that `rules/coverage.py` scores as implemented and that no code anywhere
cites. Coverage is claimed per rule *group*, so marking "601" implemented scores
all forty of its subrules as done — and that is how the coverage report came to
report rules as covered whose behaviour was missing outright. An implemented
claim with no citation behind it is the one to check first.
