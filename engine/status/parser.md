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

- **Mana core** (this push): `engine/src/rules/mana/`, ported from `cr106_mana.py`.
  - `cost.ts`: ManaSymbol, parse, mana value, colors, plus, increased_by, reduced_by, reduced_by_mana and substitute_x. Symbols are plain data with numeric kind and colors. `fromIR` reads an IR cost (v1 or v2). `costKey` is the value-equality key.
  - `pool.ts`: your `mana_pool.ts`, moved, plus `restrictionPermits` (SpendOnlyOn) and `usableFor`.
  - `payment.ts`: find_payment and can_pay, the same constraint solve with the same deterministic preference order.
  - `ir_value.ts`: your `mana_value.ts`, moved, with the same API on IR costs, now computed through `cost.ts`.
  - The old paths `cr100_game_concepts/mana_pool.ts` and `mana_value.ts` are one-line re-exports, so none of your imports changed.
  - `test_mana.py` is ported case for case (38 tests), plus 4 reduced_by_mana cases checked against Python.
  - One interface note: a payment's restriction context is `PaymentContext {types, subtypes}` (the spell's printed types), or `null` for a bare payability check. Python passed the GameObject and read its printed type line; the caller now builds the context.

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

1. `mana_plan.py`, after agreeing the interface with the rules session. Carry
   over the F61 guard. I need from you: how a permanent's mana abilities are
   listed and activated (`characteristics(game, obj).abilities` plus your
   activation entry point), and whether tapping goes through a function of
   yours. If it's easier, publish the signatures in your status file and
   I'll code against them.
2. `engine/src/rules/match/`: `matching.py`, `values.py`, `conditions.py`.

## Questions for the auditor

- IR v2 is waiting on the rules session's choice above. Nothing else is blocking.
- The web session's `/api/ir/*` reads `<data root>/ir/v1` (`MTGFISH_IR_DIR`). When v2 lands, the default export path becomes `ir/v2`, so the web session needs to switch with it. I'll flag it in the commit.
