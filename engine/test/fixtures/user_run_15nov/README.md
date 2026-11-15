# The user's live browser run, 15 Nov

These are the decks from the run that hung on the live site (`?engine=browser`). They are the user's public Archidekt decks:
- seats 1 and 2 are 27600141 (Vraska, Soul of Stone);
- seat 3 is 26926766 (Muddle, the Ever-Changing);
- seat 4 is 26074514 (Kilo, Apogee Mind).

The run used seed 0, 1,000 games and a turn cap of 25.

- `decks/*.txt`: the decklists as exported from Archidekt.
- `decks.json`: the deck specs in seat order `a_vraska, b_vraska, c_muddle, d_kilo`, written by `python -m mtgfish.tools.ir_export samples --decks <dir>`, with each commander moved into `commanders` (the exporter doesn't read Archidekt's `[Commander]` tag).
- `ir/*.json`: each card's IR document at the time of export.

On the engine before 674b258, about 1 game in 60 never finished (F65). For example, game indices 169, 190, 271, 284, 307, 360, 386, 424, 435, 497 and 581 hung. The site resolves the decks through Archidekt, so its card order, and so its game seeds, can differ for some games.

Build a `RunSetup` with:
- `decks`: `decks.json` entries in seat order;
- `cards`: every `ir/*.json` keyed by `oracle_id`;
- `runSeed: 0` and `maxRounds: 25`.
