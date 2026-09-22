# Cards that beat the rules (CR 101.1)

CR 101.1 is the golden rule: *when a card's text directly contradicts these
rules, the card takes precedence.*

An engine cannot implement that in general. Card text here compiles into a
fixed vocabulary of effects, so a card can only beat a rule the engine has
left a seam for; a card whose text contradicts a rule in a way the opcode set
cannot express does not beat the rule, it fails to parse.

This document is the list of seams, and how to write a card against each.

## Which seam a card needs

Ask what the card is contradicting.

| The card... | Seam | Lives in |
|---|---|---|
| changes what an object *is* — its power, colour, types, abilities, controller | **continuous effect** (CR 611) | `cr600_spells_and_abilities/cr613_layers.py` |
| rewrites an event before it happens — "if it would die, exile it instead" | **replacement effect** (CR 614) | `cr600_spells_and_abilities/cr614_replacement.py` |
| forbids or permits an *act* — "can't attack unless", "may cast from your graveyard" | **prohibition / permission** (CR 101.2) | `cr500_turn_structure/restrictions.py` |
| switches off a **rule of the game** — "creatures don't suffer summoning sickness" | **rule override** (CR 101.1) | `cr100_game_concepts/cr101_rule_overrides.py` |

The last row is the one people reach for too early. Most cards that *sound*
like they break a rule are really one of the first three:

- "Creatures you control have haste" grants an ability → **continuous
  effect**, not a rule override. The rule still applies; the creatures are
  just exempt from it by having haste.
- "Creatures you control can attack the turn they come under your control"
  forbids nothing and grants nothing an object carries → **rule override**.

The test is whether the card changes an *object* or changes the *game*. If
you can name the characteristic it changes, it is CR 611. If you can name the
act it forbids, it is CR 101.2. If neither, and you can name the CR rule it
suspends, it is a rule override.

## Writing a rule override

Emit one `SUSPEND_RULE` effect. There is no per-card code, and nothing to
register anywhere.

```python
Effect(
    EffectKind.SUSPEND_RULE,
    rule=Rule.SUMMONING_SICKNESS,          # the CR number being suspended
    targets=ObjectFilter(                  # for which objects
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
    ),
    text="creatures you control have no summoning sickness",
)
```

- `rule` is a member of `cr101_rule_overrides.Rule`. Its **value is the CR
  number** (`"302.6"`), so a parser that has read a rule number can look one
  up directly, and a log can print it.
- `targets` says which objects the suspension covers, `players` which
  players. A rule about an object (summoning sickness) reads `targets`; a
  rule about a player (the land-per-turn limit) reads `players`. **Neither
  set means the rule is simply off for everyone.**
- From a **static ability** on a permanent it applies while that permanent is
  on the battlefield and stops the moment it leaves — regenerated with the
  other continuous effects (CR 611.3), so nothing has to clean up after it.
- From a **resolving spell** it takes `duration` and outlives its source, the
  same way a "this turn" prohibition does.

## The rules that can be suspended

Deliberately a closed list. A rule the engine has no name for is one it cannot
suspend, and the failure mode of an open registry is *silence* — a card that
claims to switch a rule off and does not.

| `Rule` member | CR | Enforced at |
|---|---|---|
| `SUMMONING_SICKNESS` | 302.6 | `cr506_combat.can_attack`, and the {T}-ability checks in `cr601_casting` |
| `LEGEND_RULE` | 704.5j | `cr704_sba._check_legend_rule` |
| `ONE_LAND_PER_TURN` | 305.2 | `legality._land_plays` |
| `ATTACKING_TAPS` | 508.1f | `cr506_combat.declare_attackers` |

If the rule a card needs is not here, **that is a gap to fill, not a reason to
special-case the card.**

## Adding a rule to the list

Two steps, and no more:

1. Add a member to `Rule`, named for what it switches off, carrying its CR
   number as its value, with the call site in its comment.
2. At that call site, ask before enforcing:

   ```python
   if game.rule_is_suspended(Rule.THE_NEW_ONE, obj=obj) is None:
       ...enforce as before...
   ```

`rule_is_suspended` returns the override rather than a bool, so when a
simulated game does something surprising the log can say *which card* said the
rule should not apply — the same reason `prohibited()` returns the
restriction.

Then add the member to `DESCRIPTIONS`, and a test. `tests/rules/test_cr101_rule_overrides.py`
has the shape: one test that the rule applies by default, one that a card
switches it off, and one that it comes back when the card leaves.

## What this does not do

It does not make CR 101.1 true in general, and the coverage table says so.
A card that contradicts a rule with no seam still fails to parse rather than
winning. That is the honest position: the alternative is an engine that
silently ignores text it cannot honour.
