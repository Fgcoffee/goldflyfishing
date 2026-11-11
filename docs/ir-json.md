# The parser's IR as JSON (IR version 2)

The boundary between the Python parser and the TypeScript engine. The parser
runs ahead of time, server-side; the engine in the browser reads what it
produced as JSON, one file per card, and never needs the card database.

| | |
|---|---|
| Schema (the source of truth) | `schema/ir.schema.json` (JSON Schema 2020-12, one face document) |
| TypeScript types | `engine/src/ir/types.ts` and `defaults.ts`: the one TS copy, generated from the same classes as the schema. Import them; don't retype them. |
| Serving the JSON | `schema/README.md` (export, file layout, keys) |
| TypeScript reader | `engine/src/ir/decode.ts`: `complete` (fill defaults, unwrap `$int`), `memberName`, `check`, `readCard` |
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
fields, defaults included, and `memberName("CardType", 5)` gives
`"ARTIFACT|CREATURE"` for messages. The numbers are exported as
`CardTypeBits`, `ZoneValues` and so on, and `engine/src/ir/kernel-enums.test.ts`
checks that they match `engine/src/kernel/enums.ts`.

## What a card file holds

One file per card, `<oracle_id>.json`: a **card document**,
`{ir_version, oracle_id, name, card_hash, card, faces}`.

* **card**: the printed card, Python's `CardDef` with its `FaceDef`s and
  their `TypeLine`. That's the name, layout, mana value, color identity and
  Commander legality, and for each face its mana cost (and whether it has
  one at all, CR 202.1b), type line, oracle text, power, toughness, loyalty
  and defense as printed (`"*"` and `"1+*"` stay strings), colors, color
  indicator, keywords and produced mana. The engine builds characteristics
  from it. The shape matches `engine/src/kernel/card.ts`, which also adds
  `edhrec_rank`, `scryfall_uri` and `attraction_lights` (CR 717.1).
* **faces**: one face document per face, below.

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
 "ir_version": 2,
 "oracle_id": "4457ed35-7c10-48c8-9776-456485fdf070",
 "name": "Lightning Bolt",
 "face": 0,
 "face_name": "Lightning Bolt",
 "card_hash": "cbd8799e266d1b8182a241ba7be078e6",
 "fully_read": true,
 "abilities": [
  {
   "$": "Ability",
   "kind": 0,
   "effects": [
    {
     "$": "Effect",
     "kind": 50,
     "targets": {
      "$": "ObjectFilter",
      "types_any": 38,
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

`"kind": 0` is `AbilityKind.SPELL`, `50` is `EffectKind.DAMAGE`, and
`"types_any": 38` is `CardType.BATTLE | CREATURE | PLANESWALKER` (2 + 4 +
32). The names are in the schema and in `types.ts` (`AbilityKindValues`,
`EffectKindValues`, `CardTypeBits`, ...).

## Encoding rules

One rule per kind of value:

| Python | JSON |
|---|---|
| IR object (frozen dataclass) | object; `"$"` names the class. **Only fields that differ from the default are written**; a missing field takes the default the schema gives (`"default"` in the JSON Schema, `DEFAULTS` in `defaults.ts`). |
| enum member | **its number**, the same number as the Python enum and `engine/src/kernel/enums.ts`. Which enum it is comes from the field's declared type in the schema. |
| flag enum (CardType, Color, Supertype) | the OR of its bits: `CardType.ARTIFACT \| CREATURE` is `5`; empty is `0`. |
| a plain integer in an enum-typed field | `{"$int": n}`. A few `Effect.duration` values are bare ints; this keeps them distinct from enum members. `complete()` unwraps it. |
| tuple | array |
| frozenset | `{"$set": [...]}`, sorted |
| list | `{"$list": [...]}` |
| `None`, bool, int, string | themselves |

Types are never coerced. A boolean stays a boolean even in a field that
holds either (an `ObjectFilter`'s `from_top` is `true` in some cards and
`3` in others), and the encoder refuses an enum in a field that doesn't
declare one, so a number can never lose its enum. The schema's unions say
where fields hold more than one type.

## Things the engine must know

* **Keyword names are not consistently cased.** An ability printed on the
  card has `"keyword": "Haste"`; one granted by another card often has
  `"haste"`. The Python engine compares them case-insensitively, and the
  TypeScript engine must too. (`schema/usage.json` lists every keyword
  name that occurs.)
* **An unread ability is inert.** It has `unparsed: true` and must do
  nothing; it is there so it can be shown.

## Versioning

**Version 2** (current) writes enums as numbers. **Version 1** wrote
`"Zone.GRAVEYARD"` strings; it was changed before anything consumed it, at
the rules session's request, because the engine compares and masks enums
in its hottest paths.

`ir_version` is in every document. It is bumped on any change a reader of
the previous version would misread: a field renamed or removed, a default
changed, an enum member renumbered. New fields with defaults and
new enum members do not bump it, because an old reader just never sees them.

## Files

`python -m mtgfish.tools.ir_export export` writes `cache/ir/v2/`:

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
