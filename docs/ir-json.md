# The parser's IR as JSON (IR version 1)

The boundary between the Python parser and the TypeScript engine. The parser
runs ahead of time, server-side; the engine in the browser reads what it
produced as JSON, one file per card, and never needs the card database.

| | |
|---|---|
| Schema (the source of truth) | `schema/ir.schema.json` (JSON Schema 2020-12, one face document) |
| TypeScript types | `engine/src/ir/types.ts` and `defaults.ts`: the one TS copy, generated from the same classes as the schema. Import them; don't retype them. |
| Serving the JSON | `schema/README.md` (export, file layout, keys) |
| TypeScript reader | `engine/src/ir/decode.ts`: `complete` (fill defaults), `enumValue`, `check`, `readCard` |
| Samples | `schema/samples/`: every card of the four bench decks, plus `decks.json` |
| Codec | `mtgfish/ir/codec.py` |
| Generator | `mtgfish/ir/schema.py` |
| Tool | `python -m mtgfish.tools.ir_export {schema,samples,export,verify}` |

The schema and the TypeScript types are generated from the Python IR
classes, so they cannot drift from the JSON the codec writes.
`tests/ir/test_ir_json.py` fails if the committed copies are out of date.
Regenerate with `python -m mtgfish.tools.ir_export schema`, and the samples
with `python -m mtgfish.tools.ir_export samples`.

In the engine, `complete(face.abilities)` gives every IR object with all its
fields, defaults included, and `enumValue("CardType.ARTIFACT|CREATURE")`
gives the number the Python engine uses (bit and member values are exported
as `CardTypeBits`, `ZoneValues`, and so on).

## What a face holds

Everything the engine receives today, as it receives it:

* **abilities**: `Ability` objects with their effects, costs, triggers,
  conditions, filters and durations. Keyword abilities are already
  **expanded**: "Flying", "Ward {2}", "Craft with artifact {3}{W}" arrive as
  the abilities the rules package's keyword builders made of them at parse
  time. The engine does not port the builders.
* **failures**: one `ParseFailure` per ability the parser could not read
  (`text`, `reason`, how far it got). `fully_read` is `failures == []`.
* **unread abilities**: an ability that failed is still in `abilities`, so
  that it can be shown. It is inert: `unparsed: true` on the `Ability`, and
  an `EffectKind.UNPARSED` effect inside. The browser can mark it "not
  understood".

```json
{
 "ir_version": 1,
 "oracle_id": "4457ed35-7c10-48c8-9776-456485fdf070",
 "name": "Lightning Bolt",
 "face": 0,
 "face_name": "Lightning Bolt",
 "card_hash": "cbd8799e266d1b8182a241ba7be078e6",
 "fully_read": true,
 "abilities": [
  {
   "$": "Ability",
   "kind": "AbilityKind.SPELL",
   "effects": [
    {
     "$": "Effect",
     "kind": "EffectKind.DAMAGE",
     "targets": {
      "$": "ObjectFilter",
      "types_any": "CardType.BATTLE|CREATURE|PLANESWALKER",
      "includes_players": true
     },
     "amount": {"$": "Value", "constant": 3},
     "is_targeted": true,
     "text": "deal damage to any target"
    }
   ],
   "text": "this deals 3 damage to any target."
  }
 ],
 "failures": []
}
```

## Encoding rules

One rule per kind of value:

| Python | JSON |
|---|---|
| IR object (frozen dataclass) | object; `"$"` names the class. **Only fields that differ from the default are written**; a missing field takes the default the schema gives (`"default"` in the JSON Schema, `DEFAULTS` in `defaults.ts`). |
| enum member | `"Enum.MEMBER"`, e.g. `"Zone.GRAVEYARD"`. The numeric values are in `types.ts` (`ZoneValues`). |
| flag enum (CardType, Color, Supertype) | set bits joined by `\|`: `"CardType.ARTIFACT\|CREATURE"`; empty is `"CardType.NONE"`. Bit values: `CardTypeBits`. |
| tuple | array |
| frozenset | `{"$set": [...]}`, sorted |
| list | `{"$list": [...]}` |
| string that looks like an enum member | `{"$str": "..."}` |
| `None`, bool, int, other strings | themselves |

Types are never coerced. A boolean stays a boolean and an integer an
integer, even in a field that holds either (an `ObjectFilter`'s `from_top`
is `true` in some cards and `3` in others). An enum is never written as a
bare number, and a bare number in an enum-typed field stays a number (some
`Effect.duration` values are plain integers). The schema's unions say where
this happens.

## Things the engine must know

* **Keyword names are not consistently cased.** An ability printed on the
  card has `"keyword": "Haste"`; one granted by another card often has
  `"haste"`. The Python engine compares them case-insensitively, and the
  TypeScript engine must too. (`schema/usage.json` lists every keyword
  name that occurs.)
* **An unread ability is inert.** It has `unparsed: true` and must do
  nothing; it is there so it can be shown.

## Versioning

`ir_version` is in every document. It is bumped on any change a reader of
the previous version would misread: a field renamed or removed, a default
changed, an enum member renamed or renumbered. New fields with defaults and
new enum members do not bump it, because an old reader just never sees them.

## Files

`python -m mtgfish.tools.ir_export export` writes `cache/ir/v1/`:

* `<oracle_id>.json`: one **card document**,
  `{ir_version, oracle_id, name, card_hash, faces: [face, ...]}`. About
  1.6 kB per card; 60 MB for the whole pool, 13 MB gzipped. A deck's worth
  is a few tens of kB.
* `index.json`: every card and face name (lower case) to its oracle id, and
  each card's hash. It is 7 MB, so serve name lookups from the server rather
  than shipping it to the browser. A client holding a card file whose
  `card_hash` still matches the index needn't fetch it again.

`card_hash` is the parse store's hash of the whole card definition, so it
changes when the card's Scryfall text does. The export reflects the parser
and data at the time it was run; rerun it after either changes.

## Proof that the JSON is exact

`python -m mtgfish.tools.ir_export verify --schema` parses every face live
(42,027 faces of 38,800 cards). For each one it encodes, serialises to text,
parses the text, validates it against the schema and decodes it, then
compares the result with the original: both `==` and the canonical encoding,
which is type-exact. It exits non-zero on any mismatch. Last run: **0
problems**. (`repr` is not compared, because a frozenset's repr depends on
hash order.) `--schema` needs the `jsonschema` package.
