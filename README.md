# MTG Commander Goldfisher

A Magic: The Gathering Commander (EDH) simulator. It reads real cards from
their oracle text, plays four-player games under the Comprehensive Rules, and
answers the questions a deckbuilder actually asks: how fast does this deck
start, how often does the commander land, which cards are pulling their weight,
and is this swap worth a slot.

It runs as a desktop application or as a web app. Both are the same front end
over the same Python bridge.

## What it does

* **Goldfishes a deck** over thousands of games and reports win rate, stall
  rate, the mana and development curve, and when the commander lands.
* **Replays any datapoint.** Click a point on a chart and that exact game is
  played again, with a full log. Nothing is stored; the seed rebuilds it. Turns
  are numbered the way the player who took them would number them, not by the
  engine's global counter - a four-player game's fourth round is its thirteenth
  turn, and nobody means that by "turn four".
* **Shows what the engine actually understood.** The Deck tab marks every card
  by how much of its text the parser read: yellow for partly read, red for
  unread. A deck can be perfectly legal and still be measured as a deck with
  ten blanks in it, and that number is on screen before you trust any other.
* **Compares cards on the same seed** in the swap lab. Two ordinary runs cannot
  answer "is this card worth a slot" - the gap between two decks differing by
  one card is usually smaller than the gap between two runs of the same deck.
  Every variant here plays the same seeds against the same opponents.
* **Lets you drive a board by hand** in the sandbox, to see whether a card that
  did nothing was an unread ability or a rules bug. Those need different fixes.
  The sandbox is a bench rather than a game, so the rules that end games are
  switched off by default - nobody loses, a library you never built is not
  fatal, mana keeps across steps - and each one is a named switch you can put
  back when the rule itself is what you are testing.
* **Imports from Archidekt** by link, or by signing in to list your own decks.

## Getting started

Python 3.12 or newer.

```bash
pip install -r requirements-web.txt   # the engine, simulator and web app
pip install PySide6                   # only for the desktop window
pip install pytest                    # only to run the tests
```

Then build the card database once. Scryfall's published dumps are already in
`cache/scryfall`, so this needs no network and takes a few seconds:

```bash
python -m mtgfish.tools.fetch_scryfall --offline
```

Drop `--offline` to pull the current dumps from Scryfall instead, which is how
you refresh the pool when new sets are released. Either way it writes about
100 MB to `cache/cards.sqlite`, which is derived data and stays out of git.

### Run it

```bash
python -m mtgfish.ui                  # the desktop window
python -m mtgfish.web --reload        # the web app on http://127.0.0.1:8000
```

### Without a window

```bash
python -m mtgfish.tools.simulate deck.txt --games 1000
python -m mtgfish.tools.swap deck.txt --swap "Sol Ring=Mana Crypt" --games 2000
python -m mtgfish.tools.parse_report --card "Lightning Bolt"
python -m mtgfish.tools.cost deck.txt --mirror   # what a run costs in time and memory
```

A decklist is a text file, or an Archidekt URL. Most export formats are
understood, with or without quantities, set codes or categories.

### Installed rather than run from a clone

`pip install .` also puts a single `mtgfish` command on the path, which is the
same set of entry points with shorter names - `mtgfish web --reload`, `mtgfish
simulate deck.txt --games 1000`, `mtgfish fetch`. Run `mtgfish` with no
arguments for the list. The `python -m` forms above keep working either way,
and are the ones to use when working in a clone.

## Reviewing someone else's branch

Agents push branches named `claude/...`. `scripts/review.ps1` tries one without
touching your own checkout: it copies the branch into its own directory beside
the repository, merges `main` into that copy - a branch that was fine when it
was written can still break against what `main` has learned since - and runs
the tests there. The card database is shared rather than rebuilt per branch.

```powershell
.\scriptseview.ps1 list                    # what is waiting, and how far ahead
.\scriptseview.ps1 diff  <branch>          # what it changes, against main
.\scriptseview.ps1 test  <branch>          # the suites, on branch merged with main
.\scriptseview.ps1 run   <branch> -Port 8010   # open the web app on it
.\scriptseview.ps1 merge <branch>          # fast-forward main when you are happy
.\scriptseview.ps1 clean                   # remove the review copies
```

`test` skips the slow parser suite; add `-Full` for everything. `-AsIs` tests
the branch without merging `main`, which is what to use when you want to see
what the agent saw. A branch that conflicts with `main` is reported as such
rather than tested, because the merge is the thing that would land.

## What is in the repository

The card pool comes as Scryfall's own dumps - oracle cards, rulings, tagger
tags and the type catalogs, about 35 MB in `cache/scryfall` - plus the
Comprehensive Rules text. The 95 MB SQLite snapshot built from them is not
committed: it is derived, rebuilt whole each time, and a copy per refresh would
live in the history for ever. `bench/results` holds the cost-benchmark output,
so a change can be measured against the numbers it is meant to improve.

You do not have to build the database yourself. Anything that needs cards -
the desktop app, the web server, the test suite - builds it from those dumps
the first time, and rebuilds it when a commit brings newer ones. Every build
of the same dumps produces the same pool, and `cache/pool.json` records which
pool that is, so a machine quietly running a month-old card list is a test
failure rather than a mystery in the numbers.

## How it is put together

| | |
|---|---|
| `rules/` | The Comprehensive Rules, implemented. Knows nothing about oracle text, bots or statistics. Every legality check lives here. The one exception is `relaxations.py`, a default-empty set of rules an instrument may suspend; a simulated game never constructs anything but `STRICT`. |
| `parser/` | Oracle text to an effect IR made only of opcodes the rules layer already runs. It cannot express an effect the engine lacks, which is what makes a mis-parse inert rather than corrupting. |
| `data/` | The Scryfall card snapshot, and deck import. |
| `ai/` | Decision making. Enumerates its options *from the engine*, so it can never drift from the real rules. |
| `sim/` | Reproducible parallel runs, statistics, replay, the swap lab. |
| `ui/` | The shared front end, the bridge it talks to, and the sandbox. |
| `web/` | The same bridge over HTTP, with server-sent events. No Qt required. |

Two rules govern the parser, and previous attempts at this died by breaking
them. **Full consumption**: if any token of an ability is left over, the whole
ability is unparsed and never fires - an ability 90% understood is 100%
dangerous, because the missing 10% is the clause that made the card worth
playing. **No invention**: the parser may only emit opcodes the engine executes.

## Determinism

A seed determines a game completely. Game 4,712 is the same game whether the
run was 5,000 games or 50,000, and whether it ran on one core or twenty. That
is what makes replay possible without storing anything, and every run records
its engine version and card-pool hash so a replay can refuse to reconstruct a
game it would no longer reproduce faithfully.

Games that cannot end are handled rather than left to spin. A repeating cycle
is detected and shortcut to its result (CR 732.2a); a mandatory loop nothing
can break is a draw (CR 104.4b); anything else that runs away is stopped and
reported as a bug in a card, not folded into the stall rate where it would look
like a property of the deck.

## Tests

```bash
python -m pytest            # 1,271 tests
python -m pytest tests/rules
```

Tests that need real cards skip rather than fail when the card database has not
been built, so a fresh clone can still run the pure-rules suite.

## More

* [BUILD.md](BUILD.md) - packaging the desktop application, and the war stories
  behind how it is packaged.
* [DEPLOY.md](DEPLOY.md) - running the web app, in a container or behind a
  proxy, and what to settle before it is public.
* [docs/parser-gaps.md](docs/parser-gaps.md) - what the parser cannot read yet.

## Known limitations

* **Not every card is understood.** Coverage is measured over the real card
  pool and reported per deck rather than claimed. Blood Moon, for one, is not
  read at all.
* **Modal spells** need a legal target for every mode, not just the chosen
  ones, so some are ruled uncastable when they are not.
* **Log in and Manage subscription are placeholders** in the web app; they
  send nothing.
* **Card verdicts are global.** Marking a card inert in the sandbox affects
  every simulation, including other users of a shared deployment.

## Credits

Card data and images come from [Scryfall](https://scryfall.com). Magic: The
Gathering is © Wizards of the Coast. This project is unaffiliated with Wizards
of the Coast, Scryfall or Archidekt.
