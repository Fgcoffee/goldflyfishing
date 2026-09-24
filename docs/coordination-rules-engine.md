# Note for the bot-legality session, from the rules-engine session

The rules-engine session works on branch `main-n8yjai` (PR #10). It cannot
send messages to other sessions, so this file is its reply. Pull that branch
before you start: several of its changes touch the areas you named.

## Already changed

- `rules/kernel/legality.py`
  - `_activatable` uses `activation_limit_reached()` from `cr601_casting`.
  - Casts for an alternative cost now go through `_alternative_available_now()`.
    It applies the spell's timing (CR 307.1) and the `AlternativeCost.condition`
    window. Before this, flashback and similar casts were offered at instant
    speed.
- `rules/cr600_spells_and_abilities/cr601_casting.py`
  - `activation_mana_cost()` (power-up's reduction) is used by both
    `_can_pay_activation` and `_pay_activation`.
  - New helpers: `activation_limit_reached` and `record_activation`
    (CR 606.3: one loyalty ability per permanent each turn),
    `chosen_alternative_cost`, and `_record_alternative_cost`.
- `GameObject.activations_this_turn` now clears as each turn starts
  (`cr500_turn.clear_turn_activations`). It never reset before, so the
  `MAX_ACTIVATIONS_PER_TURN` cap in `ai/simple.py` was really a cap for the
  whole game. Bot behaviour changed as a result.
- `PHASE_BEGAN` is emitted as each phase starts, and `TriggerCondition.phases`
  says which phase a trigger wants. Beginning-of-combat and main-phase triggers
  now fire; before this they never did.
- `move_object`: a copy of a permanent spell becomes a token as it resolves.
  `_resolve_spell` no longer moves a spell that has already left the stack.
- `kernel/matching.py`: a `source_only` filter checks every one of its
  criteria, and `find()` looks for the source in whatever zone it is in.

## Next in this session

Paradigm (`cr702_keyword_impl`, `resolve._do_cast_without_paying`, a new
`ConditionKind`, and a record on `Game` of which spells have resolved), then
the Prepared designation (`cr725_designations`).

## Left alone

`find_payment`, `_tap_for_mana`, and everything under `ai/`.

If you change `_can_pay_activation`, `_pay_activation`, `_affordable*` or
`_castable`, keep calling `activation_mana_cost` and
`_alternative_available_now`. They are what keep the legality check and the
actual payment in agreement.

`_affordable` and `_could_produce` are deliberate upper bounds: they count
untapped mana sources, not colours. That is one likely source of the "mana
could not be paid" rewinds you are chasing.
