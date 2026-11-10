# parser session: status

Update this file with every push: what landed, what's next, and any
**Questions for the auditor** (the auditor reads it, and every commit
message, at each check-in; direct messages to the auditor are refused).

## Done

All on `claude/modest-bohr-yqrqm7`. `npm test`, `npm run typecheck` and
`python -m pytest tests/ir` are green.

- **IR v1** (`70c670b`, `faff6f4`, `5c0016d`), verified by the auditor:
  - Python codec, schema and exporter;
  - `schema/ir.schema.json` (the source of truth), `schema/README.md` and `schema/samples/`;
  - `engine/src/ir/`.
- `ab05438`: a test that the IR's enum tables match `kernel/enums.ts`.
- `2f80fe7`: `schema/usage.json`, a porting order.
- `e7c8c7d`: `mtgfish/ir/store.py`, which reads the exported IR for a deck.

## IR v2 (numeric enums): ready, waiting for the rules session

- Pushed as `536557e`, then **reverted**. While it was in flight, `layers.ts`,
  `ir_read.ts` and `test/rules/{board.ts,layers.test.ts}` (`d73eadc`) landed
  building IR objects with v1 string literals (`kind: "AbilityKind.STATIC"`,
  `types_all: "CardType.CREATURE"`, `event_kinds: { $set: ["EventKind.X"] }`).
  Against v2's numeric types that's about 50 typecheck errors and 3 failing
  tests.
- v2 itself is verified:
  - enums are numbers (the same as Python and `kernel/enums.ts`);
  - snake_case fields;
  - `{"$int": n}` for a plain int in an enum-typed field;
  - no `$str`;
  - 42,027 faces round-trip and validate, 0 problems.
- **Rules session, pick one:**
  1. You convert those literals to numbers (e.g. `AbilityKind.STATIC` from
     `kernel/enums.ts` or `AbilityKindValues.STATIC` from `ir/types.ts`), and
     tell me here. I re-land v2 the same hour, so the switch is one red-free step.
  2. Or say "go" here and I'll do the mechanical conversion in your four
     files myself, in the same commit as v2. I'll change only the literals,
     with typecheck and tests green.
- Until then, write new code with the numbers. `enumValue()` accepts both
  forms, so it works across the switch.

## Next

1. `engine/src/rules/mana/`: port `cr106_mana.py` with its tests. This doesn't
   depend on the switch; I'll use numeric enums from `kernel/enums.ts` and
   keep IR reads behind `enumValue()`.
2. `mana_plan.py`, after agreeing the interface with the rules session. Carry
   over the F61 guard.
3. `engine/src/rules/match/`: `matching.py`, `values.py`, `conditions.py`.

## Questions for the auditor

- IR v2 is waiting on the rules session's choice above. Nothing else is blocking.
