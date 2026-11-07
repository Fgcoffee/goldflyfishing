""""or" between a card type and a subtype is a disjunction, never "and".

The object filter pools its fields, all of which must match. "noncreature or
Otter spell" read as a noncreature Otter (auditor's F46, Harnesser of
Storms); "creature or Vehicle" as a creature Vehicle. Such a list is now
``ObjectFilter.any_of``, one alternative per side - and where the reader
cannot tell which words belong to which side, it is left unread.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.nouns import parse_object_filter
from mtgfish.parser.tokens import Stream


@pytest.mark.parametrize(
    "phrase",
    [
        # A negated type on one side only.
        "noncreature or Otter spell",
        # "creature" may qualify both sides or only the last.
        "Saga or enchantment creature",
        # "token" belongs to the Blood only.
        "another creature or a Blood token",
        # "nontoken" belongs to the creature only.
        "nontoken creature or Treasure",
    ],
)
def test_an_ambiguous_mixed_disjunction_is_not_read_whole(card_db, phrase):
    stream = Stream.of(phrase)
    parse_object_filter(stream)
    assert not stream.done


def _alternatives(phrase):
    stream = Stream.of(phrase)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done, phrase
    return spec, [
        (alt.types_any | alt.types_all, alt.types_all, alt.supertypes_all,
         alt.subtypes_any + alt.subtypes_all, alt.must_be_colorless)
        for alt in spec.any_of
    ]


def test_a_mixed_disjunction_is_any_of(card_db):
    from mtgfish.rules.kernel.enums import CardType, Supertype, Zone
    from mtgfish.rules.kernel.query import ControllerRelation

    none = CardType.NONE
    spec, alts = _alternatives("artifact or Goblin spell")
    assert Zone.STACK in spec.zones and not spec.types_all and not spec.subtypes_all
    assert alts == [(CardType.ARTIFACT, CardType.ARTIFACT, Supertype.NONE, (), False),
                    (none, none, Supertype.NONE, ("Goblin",), False)]

    spec, alts = _alternatives("another creature or a Treasure")
    assert spec.other_than_source
    assert [a[3] for a in alts] == [(), ("Treasure",)]

    spec, alts = _alternatives("creature or Vehicle you control")
    assert spec.controller is ControllerRelation.YOU
    assert [a[3] for a in alts] == [(), ("Vehicle",)]

    # A first noun of two types is one alternative that is both.
    artifact_creature = CardType.ARTIFACT | CardType.CREATURE
    _, alts = _alternatives("an artifact creature or a Plan")
    assert alts[0][1] == artifact_creature and alts[1][3] == ("Plan",)
    _, alts = _alternatives("Artifact creatures and Heroes you control")
    assert alts[0][1] == artifact_creature and alts[1][3] == ("Hero",)

    # Two whole phrases, each with its own identity.
    _, alts = _alternatives("artifact spells and colorless spells")
    assert alts == [(CardType.ARTIFACT, CardType.ARTIFACT, Supertype.NONE, (), False),
                    (none, none, Supertype.NONE, (), True)]
    _, alts = _alternatives("legendary creature or Equipment")
    assert alts[0][2] == Supertype.LEGENDARY and alts[1][3] == ("Equipment",)


def test_two_determiners_are_two_objects(card_db):
    """Reign of Chaos: "destroy target Plains and target white creature" is
    two targets, not one that may be either."""
    stream = Stream.of("target Plains and target white creature")
    spec = parse_object_filter(stream)
    assert spec is not None and not spec.any_of and not stream.done


@pytest.mark.parametrize(
    "phrase",
    ["Goblin or Elf creature", "artifact or creature", "artifact, enchantment, or planeswalker",
     "Mountain or Forest", "target spell or nonland permanent"],
)
def test_a_disjunction_along_one_dimension_still_reads(card_db, phrase):
    stream = Stream.of(phrase)
    assert parse_object_filter(stream) is not None and stream.done


@pytest.mark.parametrize(
    "name", ["Harnesser of Storms", "Satsuki, the Living Lore", "Anje, Maid of Dishonor"],
)
def test_the_ambiguous_cards_stay_unread(card_db, name):
    assert not parse_card(card_db.lookup(name)).fully_parsed


def test_affinity_counts_its_quality(card_db):
    from mtgfish.rules.kernel.enums import CardType, Supertype
    from mtgfish.rules.kernel.query import ControllerRelation

    def counted(name):
        (ability,) = [a for a in parse_card(card_db.lookup(name)).faces[0].abilities
                      if a.keyword == "Affinity"]
        return ability.effects[0].amount.operands[0].filter

    snow_lands = counted("Icebreaker Kraken")
    assert snow_lands.types_all == CardType.LAND and snow_lands.supertypes_all == Supertype.SNOW
    assert snow_lands.controller is ControllerRelation.YOU
    assert counted("Brine Giant").types_all == CardType.ENCHANTMENT
    assert counted("Frogmite").types_all == CardType.ARTIFACT
