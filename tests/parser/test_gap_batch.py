"""The six structural gaps from docs/parser-gaps.md, pinned.

Each of these was a whole *category* of card rather than a single template, so
each test names the category and one card that proves it. The point of pinning
them is that grammar work tends to trade one template for another, and a
category regressing is much harder to notice than a card regressing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.nouns import parse_object_filter, parse_value, singular
from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.effects import EffectKind
from mtgfish.rules.enums import CardType
from mtgfish.rules.query import ValueKind


def _reads(text, parser=parse_effects):
    """Whether the grammar reads the whole of ``text`` and nothing less."""
    stream = Stream.of(text)
    result = parser(stream)
    return result is not None and stream.done


# ---------------------------------------------------------------------------
# 1. Noun phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "artifact, enchantment, or planeswalker",
        "artifact, creature, or land",
        "instant or sorcery spell",
        "creature or planeswalker you control",
    ],
)
def test_a_comma_separated_type_list_is_a_disjunction(card_db, phrase):
    """"or" is written once, before the last item only. Requiring a
    conjunction at every step stopped dead at the first comma."""
    card_db.registry()
    stream = Stream.of(phrase)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.types_any, "a list of types must be ANY, never ALL"
    assert not spec.types_all, "read as ALL it would match almost nothing"


@pytest.mark.parametrize(
    ("plural", "expected"),
    [("Goblins", "Goblin"), ("Elves", "Elf"), ("Ninjas", "Ninja"), ("Allies", "Ally")],
)
def test_plural_creature_types_are_understood(card_db, plural, expected):
    """The registry holds singulars and oracle text pluralises freely, so
    every tribal card in the format failed on its own creature type."""
    card_db.registry()
    assert singular(plural) == expected


def test_a_leading_state_adjective_reads(card_db):
    card_db.registry()
    stream = Stream.of("Attacking Ninjas you control")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.attacking is True
    assert spec.subtypes_all == ("Ninja",)


def test_a_subtype_disjunction_is_not_a_conjunction(card_db):
    card_db.registry()
    stream = Stream.of("Elf or Goblin creature")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert set(spec.subtypes_any) == {"Elf", "Goblin"}
    assert not spec.subtypes_all, "nothing is both an Elf and a Goblin"


def test_the_top_of_a_library_is_a_position_not_just_a_zone(card_db):
    """CR 401.2: a library is ordered. An effect on "the top card" must not be
    free to pick any card in the library."""
    card_db.registry()
    stream = Stream.of("the top card of your library")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.from_top == 1

    stream = Stream.of("the top three cards of their library")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.from_top == 3


def test_a_superlative_reads_over_a_filter(card_db):
    card_db.registry()
    stream = Stream.of("the greatest mana value among permanents you control")
    value = parse_value(stream)
    assert value is not None and stream.done
    assert value.kind is ValueKind.GREATEST_AMONG
    assert value.filter is not None
    assert value.operands and value.operands[0].kind is ValueKind.MANA_VALUE


# ---------------------------------------------------------------------------
# 2. Conditions
# ---------------------------------------------------------------------------


def test_unless_modifies_the_effect_before_it(card_db):
    """The dangerous failure, not the loud one.

    Read as a sibling clause, "draw a card unless that player pays {1}" became
    "draw a card" *and* something else entirely - Rhystic Study parsed into a
    cost increase, which is a completely different card that still ran.
    """
    card_db.registry()
    stream = Stream.of("you may draw a card unless that player pays {1}.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done
    assert len(effects) == 1, "the unless clause is not a second effect"

    kinds = {node.kind for effect in effects for node in effect.walk()}
    assert EffectKind.UNLESS_PAYS in kinds
    assert EffectKind.MODIFY_COST not in kinds, "this is not a cost increase"


def test_unless_carries_the_cost_that_stops_it(card_db):
    card_db.registry()
    effects = parse_effects(
        Stream.of("you may draw a card unless that player pays {1}.")
    )
    escape = next(
        node
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.UNLESS_PAYS
    )
    assert escape.pay_cost is not None
    assert escape.children, "the effect it guards must survive"


@pytest.mark.parametrize(
    "text",
    [
        "This creature gets +1/+1 as long as you control an artifact.",
        "As long as you control an artifact, this creature gets +1/+1.",
        "This creature has flying as long as you control an artifact.",
    ],
)
def test_as_long_as_reads_on_either_side(card_db, text):
    """Oracle text puts the condition before or after the effect and means the
    same thing. Only the leading form was read, and the trailing one is the
    commoner of the two."""
    card_db.registry()
    assert _reads(text)


# ---------------------------------------------------------------------------
# 3. Trigger conditions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Whenever this creature or another creature you control dies, draw a card",
        "Whenever this creature is dealt damage, you gain 2 life",
        "Whenever this creature becomes the target of a spell, draw a card",
        "Whenever a creature you control attacks or blocks, draw a card",
        "Whenever this creature becomes untapped, draw a card",
    ],
)
def test_trigger_conditions_that_used_to_give_up_immediately(card_db, text):
    card_db.registry()
    stream = Stream.of(text)
    assert parse_trigger(stream) is not None


def test_a_disjunctive_subject_includes_the_source(card_db):
    """"this creature or another creature you control" is every creature you
    control - the union of the source and the others."""
    card_db.registry()
    trigger = parse_trigger(
        Stream.of(
            "Whenever this creature or another creature you control dies, draw a card"
        )
    )
    assert trigger is not None
    assert trigger.subject is not None
    assert not trigger.subject.other_than_source


# ---------------------------------------------------------------------------
# 4. Characteristic-defining abilities
# ---------------------------------------------------------------------------


def test_a_defined_pt_is_marked_characteristic_defining(card_db):
    """CR 604.3 puts these in layer 7a, before every other P/T effect. Read as
    an ordinary setting effect it would land in 7b and overwrite counters."""
    from mtgfish.parser import parse_card

    card = card_db.lookup("Nightmare")
    assert card is not None
    abilities = [a for face in parse_card(card).faces for a in face.abilities]
    defined = [a for a in abilities if a.is_characteristic_defining]
    assert defined, [a.text for a in abilities]

    effect = defined[0].effects[0]
    assert effect.kind is EffectKind.SET_PT
    assert effect.amount.kind is ValueKind.COUNT


def test_a_cda_functions_outside_the_battlefield(card_db):
    """CR 604.3: a CDA applies in every zone."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.enums import Zone

    card = card_db.lookup("Nightmare")
    defined = [
        a
        for face in parse_card(card).faces
        for a in face.abilities
        if a.is_characteristic_defining
    ]
    assert Zone.GRAVEYARD in defined[0].functions_in


# ---------------------------------------------------------------------------
# 5. Prevention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Prevent all combat damage that would be dealt this turn.",
        "Prevent all combat damage that would be dealt to this creature.",
        "Prevent the next 3 damage that would be dealt to target creature this turn.",
        "Prevent all damage that would be dealt to you this turn.",
    ],
)
def test_prevention_shapes(card_db, text):
    card_db.registry()
    assert _reads(text)


# ---------------------------------------------------------------------------
# 6. Mana restrictions
# ---------------------------------------------------------------------------


def test_restricted_mana_cannot_pay_for_the_wrong_spell(card_db, tmp_path):
    """The whole point of reading the rider.

    Mana that is supposed to be narrow being spendable on anything is a
    straightforward overstatement of how fast a deck is - and it would show up
    in a run as nothing but good draws.
    """
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules.cr106_mana import ManaKind, SpendOnlyOn
    from mtgfish.rules.enums import Color, Zone
    from mtgfish.rules.query import ObjectFilter
    from mtgfish.ui.sandbox import Sandbox

    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    only_creatures = SpendOnlyOn(
        key="creature spells",
        filter=ObjectFilter(
            types_all=CardType.CREATURE, zones=frozenset({Zone.STACK})
        ),
    )
    pool = box.game.player(0).mana_pool
    for color in (Color.NONE, Color.RED, Color.GREEN):
        pool.add(ManaKind(color, restriction=only_creatures), 4)

    box.put("Grizzly Bears", "hand", 0)
    box.put("Lightning Bolt", "hand", 0)

    offered = [action["description"] for action in box.legal()]
    assert any("Grizzly Bears" in text for text in offered)
    assert not any("Lightning Bolt" in text for text in offered), (
        "restricted mana must not make a noncreature spell look castable"
    )
