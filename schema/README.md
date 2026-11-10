# `schema/`: the card IR as JSON

What the parser produces for each card, as versioned JSON. The engine in the
browser reads it instead of oracle text. The format and its encoding rules
are in [`docs/ir-json.md`](../docs/ir-json.md).

| File | What it is |
|---|---|
| `ir.schema.json` | JSON Schema for one face document. **The source of truth.** |
| `samples/` | Every card of the four bench decks (`bench/decks/*.txt`), plus `samples/decks.json`. For the engine's tests. |

The TypeScript types for the same shapes are `engine/src/ir/types.ts` and
`defaults.ts`. They are generated from the same Python classes as the schema
and are the only TS copy.

## Exporting

```bash
python -m mtgfish.tools.ir_export export                 # every card -> cache/ir/v1/
python -m mtgfish.tools.ir_export export --out DIR       # ... or into DIR
python -m mtgfish.tools.ir_export samples                # the bench decks -> schema/samples/
python -m mtgfish.tools.ir_export schema                 # regenerate the schema and the TS types
python -m mtgfish.tools.ir_export verify --schema        # every face round-trips; 0 problems expected
```

Rerun `export` after the parser or the card data changes. It writes every
card, about 75 seconds on four cores. To serve one deck, look up its cards
in `index.json` and send their card files.

## Layout and keys

```
cache/ir/v1/
  index.json              names -> oracle ids, and each card's hash
  <oracle_id>.json        one card document, all its faces
```

* **`ir_version`** is in every document and in the path (`v1/`). A reader
  must refuse a version it doesn't know. It changes only when a reader of
  the old JSON would misread the new.
* **`oracle_id`** names the card: Scryfall's oracle id, shared by every
  printing. The file name is `<oracle_id>.json`.
* **`face`** is the face's index within the card (0, or 0 and 1 for a
  double-faced, split, adventure or flip card), in `faces[]` order.
* **`card_hash`** is a hash of the whole card definition, which changes when
  the card's text does. A client holding a file whose `card_hash` matches
  `index.json` needn't fetch it again.

`index.json`:

```json
{
 "ir_version": 1,
 "built_at": "2026-11-10T12:20:00",
 "snapshot": "<card data hash>",
 "cards": {"4457ed35-7c10-48c8-9776-456485fdf070": {"name": "Lightning Bolt", "card_hash": "cbd8799e266d1b8182a241ba7be078e6"}},
 "names": {"lightning bolt": "4457ed35-7c10-48c8-9776-456485fdf070"}
}
```

`names` holds each card's full name and each face's name, in lower case. It
is 7 MB for the whole pool, so resolve names on the server and send only the
card files a deck needs (about 1.6 kB per card; 60 MB for all 38,800 cards,
13 MB gzipped).

A card document (`<oracle_id>.json`):

```json
{
 "ir_version": 1,
 "oracle_id": "4457ed35-7c10-48c8-9776-456485fdf070",
 "name": "Lightning Bolt",
 "card_hash": "cbd8799e266d1b8182a241ba7be078e6",
 "faces": [
  {
   "ir_version": 1,
   "oracle_id": "4457ed35-7c10-48c8-9776-456485fdf070",
   "name": "Lightning Bolt",
   "face": 0,
   "face_name": "Lightning Bolt",
   "card_hash": "cbd8799e266d1b8182a241ba7be078e6",
   "fully_read": true,
   "abilities": [{"$": "Ability", "kind": "AbilityKind.SPELL", "effects": ["..."]}],
   "failures": []
  }
 ]
}
```

`fully_read` is false when any ability could not be read. That ability is
still in `abilities` (inert, `unparsed: true`), and `failures` says what
failed, so the browser can show the card as partly understood.

The export holds what the parser produces today, misreads included. Fixes
to card readings are paused during the port.
