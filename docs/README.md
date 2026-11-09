# What is in `docs/`

Three kinds of file live here. Most of them are written for whoever works on
the parser or the rules engine next, whether that is a person or an agent.

## Start here

| File | What it is |
|---|---|
| [parser-working-rules.md](parser-working-rules.md) | The rules every change to `mtgfish/parser/` is held to, and how to check a change against them. Read this before touching the parser. |
| [parser-gaps.md](parser-gaps.md) | Why the parse rate is what it is: the structural reasons some cards are not read yet. `tests/parser/test_gap_batch.py` pins the gaps it describes. |
| [rules-citations.md](rules-citations.md) | How the code cites the Comprehensive Rules by number, how those citations are checked, and how to compare two rules releases. |
| [rule-overrides.md](rule-overrides.md) | Cards that beat the rules (CR 101.1): the places the engine lets card text override a rule, and how to write a card against each. |

These are written by hand. Change them along with the code they describe.

## Generated reports

Written by a tool. Do not edit them by hand: run the tool again.

| File | Made by | What it is |
|---|---|---|
| [engine-gaps.md](engine-gaps.md), [engine-gaps.csv](engine-gaps.csv) | `python -m mtgfish.tools.engine_gaps` | Every format-legal card that does not work, and whether the fault is in the rules engine or in how the parser wired it. The CSV is the same data, one row per card. |
| [cr-index.txt](cr-index.txt) | `python -m mtgfish.tools.rules index --only-cited --out docs/cr-index.txt` | The Comprehensive Rules by number, with the code that cites each rule. |

## Spot checks

[spot-check/](spot-check/) holds samples of the parser's output that were
checked by hand. Each one is a dated set of files. The cards are drawn with
`python -m mtgfish.tools.spot_check sample`, and the verdicts are filled in
by hand:

| File | What it is |
|---|---|
| `<date>-report.md` | The findings. Start here. |
| `<date>.md` | The full sheet: every sampled card, its text, the parser's reading and the verdict. |
| `<date>.verdicts` | The verdicts alone. |
| `<date>.json` | The cards in the sample, by name and Scryfall oracle id. |

The open problems a spot check finds are tracked in
[../audit/AUDIT_LOG.md](../audit/AUDIT_LOG.md), not here.
