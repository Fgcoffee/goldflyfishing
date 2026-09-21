# Building the executable

```bash
python -m PyInstaller build/mtgfish.spec --noconfirm --distpath dist --workpath build/work
```

Output: `dist/MTG Goldfisher/MTG Goldfisher.exe` (one folder, ~525 MB, of
which 99 MB is the card database and most of the rest is QtWebEngine).

Ship the whole `MTG Goldfisher` folder. The `.exe` alone will not run.

## Why one folder and not one file

QtWebEngine launches a separate helper process for the renderer, and that
helper has to find Qt's resources and locales on disk. A one-file build unpacks
them to a temporary directory the helper may not be able to see, and the
symptom is a blank white window with nothing in any log. Not worth the tidier
output.

## Why there is a launcher script

`mtgfish/ui/__main__.py` cannot be the entry point. PyInstaller runs the entry
script as `__main__` with no package around it, so its relative imports fail
with "attempted relative import with no known parent package" before anything
else happens. `build/launcher.py` imports absolutely, and also writes a
traceback to the user's data directory - a windowed build has no console, so an
early crash otherwise makes the process vanish with no message anywhere.

## The card database

Shipped read-only beside the executable and copied to the per-user data
directory on first launch (`mtgfish/bootstrap.py`). It is copied rather than
read in place because the program writes to it, and writing into the install
directory is what breaks a packaged Windows application for every user except
the one who installed it.

Data lives in `%LOCALAPPDATA%\mtgfish` when packaged, and in `cache/` when
running from source. `MTGFISH_DATA_DIR` overrides both, which is how the build
is smoke-tested against a clean profile.

## Smoke test

```bash
MTGFISH_DATA_DIR=/tmp/mtgfish-test "./dist/MTG Goldfisher/MTG Goldfisher.exe"
```

A first run against an empty directory should copy `cards.sqlite` into it and
open a window titled "MTG Commander Goldfisher". If the window is blank, the
Chromium sandbox failed to start - the in-app diagnostic page explains the
`QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox` workaround.

## Headless mode

```bash
"dist/MTG Goldfisher/MTG Goldfisher.exe" --simulate deck.txt --games 1000
```

Runs without a window. Useful for batch runs, and it is the only way to
exercise the *frozen* binary's worker-process path, which is where the
extra-windows bug lived.

## The extra windows

The simulator runs games across a process pool. On Windows a new process is
started by re-executing the program, and a frozen build cannot tell "I am a
worker" from "I was double-clicked" - so every worker opened its own copy of
the application window.

`multiprocessing.freeze_support()`, called as the first statement in
`build/launcher.py`, is the fix: in a worker it runs the assigned task and
exits instead of falling through to `main()`.

Verified by running 600 games through the packaged binary and counting:

```
total processes: 24     (a 23-worker pool plus the parent)
with a window  : 0
```

## The swap lab

A fifth tab, **Swap lab**. Load the cards from your deck, pick one, type what
you would rather play in its place, and every variant runs the *same seed*
against the *same opponents*.

That last part is the whole point. Two ordinary 10,000-game runs cannot answer
"is this card worth a slot": the gap between two decks that differ by one card
is usually smaller than the gap between two runs of the same deck, so the noise
wins. Holding the seed fixed takes run-to-run variance out of the comparison.
It is not a controlled experiment - change a card and the library is a
different library, so the draws diverge from the first shuffle - but the part
that was drowning the signal is gone.

### How the variant count works

A **slot** is one card in the deck plus the candidates to replace it.
Candidates inside a slot are always alternatives: they replace the same card,
so no deck can hold two of them. What the checkbox changes is what happens
*across* slots.

| | Ten slots, one candidate each | One slot, five candidates | Five slots, one candidate each |
|---|---|---|---|
| **Apply all swaps together** (default) | 1 deck with ten changes | 5 decks | 1 deck with five changes |
| **Try each on its own** | 10 decks | 5 decks | 5 decks |

Combined mode *multiplies*, which is why the plan line tells you the deck
count before you press the button: four slots with three candidates each is
eighty-one decks, not the twelve it looks like. Past `MAX_VARIANTS` the plan is
refused with the number, rather than quietly running part of it.

The two modes answer different questions. Combined asks "is this list better
than that list". Separate asks "which of these swaps is worth making" - which
combined cannot tell you, because if the fully-swapped deck wins you still do
not know which change did it.

### Same thing without a window

```bash
python -m mtgfish.tools.swap deck.txt \
    --swap "Sol Ring=Mana Crypt,Mana Vault" \
    --swap "Llanowar Elves=Birds of Paradise" \
    --games 2000 --separate
```

### Opening a variant

The **Open** button on any row loads that variant onto the ordinary Results
screen, charts and all. It carries the variant's own run config with it, so a
replay launched from those charts rebuilds *that* deck - without which it would
re-simulate the baseline and present a game that never happened.

## Why runs used to take forever

A thousand-game run managed forty games overnight. It was not slow; it was
hanging, and the cause was one wrong default.

`CostComponent.amount` defaults to zero, and the parser built every
sacrifice-yourself cost without setting it. "Sacrifice this artifact: Draw a
card" was therefore paid by sacrificing *zero* permanents: the permanent stayed
on the battlefield, the cost could immediately be paid again, and the bot did -
4,861 times in one step, until the priority loop hit its 2,000-round cap. The
stack reached 4,500 objects, and because trigger collection walked every object
on every event, each of those objects was then visited on every subsequent
event. 36 million object visits for 15,000 events.

Nothing was wrong with the rules engine and nothing was wrong with the parse.
The ability existed, the opcode was right, and the cost was charged to nobody.
88 cost components among the top 2,000 cards had it - including Evolving Wilds,
which is in most decks, which is why *every* real deck ground to a halt while a
synthetic test deck ran fine.

Fixed in the parser (the amount is one) and in the engine
(`costs.required_amount` floors a consuming cost at one, so a future clause
that forgets cannot make an ability free).

### The backstop

A simulator that hangs is worse than one that reports a bad number, so a game
now also has an action budget (`priority.ACTION_BUDGET`). Spending it ends the
game, records what was looping on the game record, and the Results screen shows
it above the charts. A runaway is a bug in a card, not a property of the deck,
so it is reported separately rather than folded into the stall rate where it
would look like the list's fault.

## Speed

With the hang fixed, the remaining work was ordinary profiling.

| what | why it cost so much |
|---|---|
| `IntFlag` arithmetic | `has_type`, `is_creature` and friends are called three million times a game, and `IntFlag.__and__` *constructs a new enum member* per call. Comparing the underlying ints is the same test; this alone was ~23% of the run. |
| Trigger collection | Every event walked every object in six zones asking "do you watch this?". Now indexed by event kind, rebuilt only when the epoch or the object population changes. |
| Layer recomputation | `_live_effects` rebuilt the whole continuous-effect list once *per layer*, eight times per board. Built once and grouped by layer; `_still_exists` already provided the CR 613.6 re-check that rebuilding was thought to be for. |
| `sorted(by_id)` | Inside the per-effect loop, so a 40-element list was sorted 2.6 million times a game. Hoisted. |

Measured on a 63-spell deck of top-EDHREC cards, four-handed:

```
fixed startup ~32s (each worker loads the card database once)
marginal      0.43s/game across all workers

1,000 games  ~8 minutes
10,000 games ~72 minutes
```

The startup is per *run*, not per game, so short runs look disproportionately
slow - 20 games is mostly waiting for workers to load. Runs under 32 games are
single-threaded for that reason.

## Two bugs found by using it

**The swap lab died with a SQLite thread error.** A sqlite connection belongs
to the thread that opened it. The bridge builds one card database on the UI
thread and keeps it for the life of the window, and `_lab_worker` read
`db.content_hash` *from the worker thread* - so the lab failed before playing a
game. Simulate got away with it by accident, reading the same property on the
UI thread and passing the string across.

Fixed three ways, because there are three ways to make the mistake:

* `content_hash` is read once at construction and remembered, so the commonest
  cross-thread read cannot happen at all;
* the bridge reads it on the UI thread and hands the worker a value;
* the per-run worker cache is keyed by thread, so a database opened by one
  run's thread is never handed to another's. That one was latent: press
  Simulate, then run a small comparison, and the second would have inherited
  the first thread's connection.

**Clones entered as themselves.** "You may have this creature enter as a copy
of a creature you control" parsed perfectly - `OPTIONAL` wrapping
`COPY_PERMANENT`, targets read, nothing marked unparsed - and did nothing,
because the entry-replacement handler knew tapping and counters and this was a
third shape. Thirteen clones in the played pool - Glasspool Mimic, Clever
Impersonator, Phyrexian Metamorph, Spark Double, Sculpting Steel, Mirrormade
and the rest - entered as their printed selves, which for most of them means a
0/0 that dies to state-based actions immediately.

Same shape as the Equipment bug: a correct parse, a correct opcode, and nothing
joining them. The coverage report scored it as a success both times.

With no agent to ask, the copy is the biggest creature (ties by mana value then
id, so replays stay exact) and declining is not offered - a clone that declines
is a 0/0 that dies before anyone can respond. An agent may override it through
`choose_copy_target`. The "except it's a Shapeshifter Rogue in addition to its
other types" tail is consumed but not applied, so the copy is faithful except
for added subtypes; that costs a tribal interaction, where not copying costs
the whole card.

## Infinite loops

An infinite used to end the *measurement* instead of the game. A mandatory
loop - Sanguine Bond and Exquisite Blood - resolved trigger after trigger until
the priority loop's 2,000-round cap threw the game out as a runaway with no
winner. An optional one was cut off by the bot's cap of eight activations per
ability, which was exactly the eight cycles a loop needs to be recognised.

`rules/kernel/loops.py` now does what CR 732.2a allows a player to do: skip to the
result. Every action and every resolution in a priority window is a *step*;
when the same cycle of steps repeats **eight times**, on a board of the same
shape, changing the game by the same amounts each time, it is a loop, and its
per-cycle change is applied as many more times as the loop would be run:

* **An optional loop** runs exactly far enough to kill every opponent it can
  reach, never far enough to kill the player running it, and never past what
  it spends (cards, counters, tokens, floating mana). A loop that would kill its
  controller first is declined.
* **A loop aimed at one opponent** - "target opponent loses that much life", a
  pinger - drains that opponent to exactly lethal, then is re-aimed at the next,
  because the target is chosen again every time. Forty per player, not forty in
  total and not a thousand.
* **A resource-only loop** (mana, counters, tokens, life gain) runs to a cap:
  1,000 of most things, 100 tokens.
* **A loop that changes nothing** is stopped and listed. It is usually a card
  read wrong rather than a real combo.
* **A mandatory loop nothing ends** is a draw (CR 104.4b), counted as
  `LOOP_DRAW`, not as a stall-out.

The bounded repeat that looks like a loop - thirty tokens dying under Zulaport
Cutthroat - is not extended into a kill it never had: the shrinking stack
changes the board's shape every cycle, and anything a cycle spends caps the
shortcut at what is there. The Results screen lists every loop found and what
was done with it.

Not scaled: loops that span turns (the turn cap still handles those), and token
*copies* made by a scaled loop, which come out as their printed card.

## Three multiplayer bugs the loop tests found

All three are invisible in a two-player game, which is why nothing caught them.

**Tokens never set off death triggers.** A token leaving the battlefield had
its zone moved to the graveyard *before* the "dies" event went out, so "whenever
a creature you control dies" asked about a token that no longer looked like a
creature on the battlefield, and said no. Zulaport Cutthroat, Blood Artist and
every other aristocrat triggered off cards and never off tokens - most of what
they do in Commander. A token's own "when this dies" never fired either.

**"Target opponent" hit every opponent, and "target player" hit nobody.**
Triggered abilities never offered players as targets, so Sanguine Bond went on
the stack with no target; and the resolver ignored whatever *had* been chosen
and resolved the words instead. One Bond drain took all three opponents from 40
to 39, and Sign in Blood drew no cards. Spells had the second half of the bug
too.

**The bot aimed "target player loses 1 life" at itself.** It could not tell its
own player target from anyone else's. It now aims harmful player effects at the
opponent with the lowest life, and helpful ones at itself.

Also corrected: a combo kill set off by combat damage (Exquisite Blood seeing
the hit) was reported as a combat win. It is now a noncombat win.

Results from before these fixes undercount aristocrats, drain and "target
opponent" decks, and overcount how hard single-target drains hit a pod.
