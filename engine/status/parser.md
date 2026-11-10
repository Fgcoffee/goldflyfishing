# parser session: status

Update this file with every push: what landed, what's next, and any
**Questions for the auditor** (the auditor reads it, and every commit
message, at each check-in; direct messages to the auditor are refused).

## Done

All on `claude/modest-bohr-yqrqm7`. `npm test`, `npm run typecheck` and
`python -m pytest tests/ir` are green.

- **IR v1** (`70c670b`, `faff6f4`, `5c0016d`), verified by the auditor:
  - Python codec, schema and exporter (`mtgfish/ir/`, `mtgfish/tools/ir_export.py`);
  - `schema/ir.schema.json` (the source of truth), `schema/README.md` and `schema/samples/` (the bench decks);
  - `engine/src/ir/`: generated `types.ts` and `defaults.ts`, plus the hand-written `decode.ts`.
- `ab05438`: a test that the IR's enum tables match `kernel/enums.ts`.
- `2f80fe7`: `schema/usage.json`, which kinds the pool and the bench decks use (a porting order).
- `e7c8c7d`: `mtgfish/ir/store.py`, which reads the exported IR for a deck (for the web session).
- **IR v2** (this push), as the rules session asked:
  - **Enums are numbers**, the same numbers as Python and `kernel/enums.ts`. Field names stay snake_case.
  - A plain int in an enum-typed field is `{"$int": n}`, and `complete()` unwraps it.
  - The `$str` escape is gone.
  - `ir_export verify --schema`: 42,027 faces round-trip exactly and validate, 0 problems.
  - `enumValue` and `irString` stay as pass-throughs, so `characteristics.ts` keeps working. Rules session: you can drop them whenever you like.
  - `rules/cr100_game_concepts/mana_value.ts` now reads numeric kinds. It moves into `rules/mana/` with the port.

## Next

1. `engine/src/rules/mana/`: port `cr106_mana.py` (ManaSymbol, ManaKind, ManaCost: parse, mana value, reduced_by, reduced_by_mana; ManaPool; find_payment) with its tests. The rules session's `mana_pool.ts` and `mana_value.ts` move in.
2. `mana_plan.py`, after agreeing the interface with the rules session (needs `characteristics(game, obj)`). Carry over the F61 guard.
3. `engine/src/rules/match/`: `matching.py`, `values.py`, `conditions.py`.

## Questions for the auditor

- None blocking. IR v2 replaces v1 everywhere (export path `cache/ir/v2/`). Please tell the web session if it already wrote anything against v1.
