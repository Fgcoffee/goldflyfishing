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

- **UTF-8 and the code fingerprint** (this push):
  - Every read and write in `ir_export.py` and `mtgfish/ir/` names `encoding="utf-8"`, and writes use `newline="\n"`. That fixes the Windows cp1252 crash on names like "ō", and the generated files are byte-identical on every OS. An AST test keeps it that way.
  - `index.json` now records `code`, a fingerprint of the parser, rules, card definitions and the IR codec (`mtgfish.ir.store.code_fingerprint()`).
  - `IRStore` treats an export written by other code as stale: it parses live and logs once.
  - Web session: put `code` in your cache keys next to `card_hash`. It's documented in `schema/README.md`.

- **Printed card data** (this push): every card document now has `card`, Python's `CardDef` / `FaceDef` / `TypeLine`, encoded like the abilities.
  - The schema's root is now the card document; the face document is `$defs/FaceDocument`.
  - `verify --schema` round-trips every printed card too: 42,027 faces of 38,800 cards, 0 problems. Samples are refreshed.
  - Rules session: it matches `kernel/card.ts` field for field, plus three fields you don't have: `edhrec_rank`, `scryfall_uri` and `attraction_lights` (CR 717.1, Attractions). Add them to `CardDef` or ignore them.
  - Until IR v2 lands, its enums (layout, colors, types) are v1 strings like the rest; read them with `enumValue`.

## IR v2 (numeric enums): landed

- Re-landed (this push) after the rules session's `799bcf0`. Enums are numbers, the same numbers as Python and `kernel/enums.ts`, printed card included. Field names are snake_case, a plain int in an enum-typed field is `{"$int": n}`, and `$str` is gone.
- `verify --schema`: 42,027 faces of 38,800 cards and every printed card round-trip and validate, 0 problems. Samples and `usage.json` are refreshed.
- `enumValue()` and `irString()` still accept both forms.
- **Web session:** the default export path is now `cache/ir/v2/` (`ir_version` 2). Point `MTGFISH_IR_DIR` and the `/ir/v1/...` URLs at v2, or serve both. The card files' shape is unchanged apart from numbers in place of enum strings, and the new `card` key.

## Mana planning (this push): the core is in, the game side needs you

`engine/src/rules/mana/plan.ts` ports `mana_plan.py`.
- **Done, game-independent:**
  - `planPayment({pool, life, cost, sources, context})`: the same search, with cheap single-purpose sources first, ceilings for pruning, the search budget, and trimming of unneeded activations. It keeps one life point back.
  - `sortSourceOptions`; drawbacks read from the IR (`costDrawback`, `effectDrawback`, `optionDrawback`, `modalEffect`).
  - `executePlan`, with the **F61 guard**: every source is re-checked through the host before it's activated, and `PlanNoLongerHolds` is thrown if one has gone.
  - Tests rebuild the Python board scenarios as options: pain land, basic before pain, Signet, any colour, only what's needed, Phyrexian life, and the F61 re-check.
- **Rules session, please implement `ManaPlanHost<Game>`** (in `plan.ts`). It's the part of `mana_plan.py` that needs casting and resolution, which are yours:
  - `manaOptions(game, player)`: Python `mana_options` with `_outcomes` / `_produced` / `_predict`. For each untapped permanent's mana abilities that `_can_pay_activation`, give one `ManaOption` per mode × colour choice, with `produces` predicted the way `_do_add_mana` would add it, `manaCost` from `activation_mana_cost`, `drawback: optionDrawback(ability, modes)`, and each source's list `sortSourceOptions`-ed.
  - `stillActivatable(game, option)`: Python `_still_activatable`. The F61 check: on the battlefield, still has the ability (by identity), and can still pay.
  - `activate(game, player, option, ability)`: `_pay_activation` plus `resolve_mana_ability` (modes via `choose_modes`, `mana_color` = `option.color`), then `resolve_mana_triggers`.
  - Then `canProduce(host, game, player, …)` and `executePlan(host, …)` are Python's `can_produce` and `execute_plan`. Tell me if you'd rather I write the host myself once casting lands.

## Next

1. ~~`mana_plan.py`~~: core done, host waiting on the rules session (above)., after agreeing the interface with the rules session. Carry
   over the F61 guard. I need from you: how a permanent's mana abilities are
   listed and activated (`characteristics(game, obj).abilities` plus your
   activation entry point), and whether tapping goes through a function of
   yours. If it's easier, publish the signatures in your status file and
   I'll code against them.
2. `engine/src/rules/match/`: `matching.py`, `values.py`, `conditions.py`.

## Questions for the auditor

- IR v2 is waiting on the rules session's choice above. Nothing else is blocking.
- The web session's `/api/ir/*` reads `<data root>/ir/v1` (`MTGFISH_IR_DIR`). When v2 lands, the default export path becomes `ir/v2`, so the web session needs to switch with it. I'll flag it in the commit.
