""""or" between a card type and a subtype is left unread, not read as "and".

The object filter pools its fields, all of which must match, and has no
disjunction across them. "noncreature or Otter spell" read as a noncreature
Otter (auditor's F46, Harnesser of Storms); "creature or Vehicle" as a
creature Vehicle. A keyword's quality read only in part ("Enchant creature
or Vehicle" as "Enchant creature") is refused the same way.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.nouns import parse_object_filter
from mtgfish.parser.tokens import Stream


@pytest.mark.parametrize(
    "phrase",
    [
        "noncreature or Otter spell",
        "artifact or Goblin spell",
        "another creature or a Treasure",
        "Saga or enchantment creature",
        "artifact spells and colorless spells",
    ],
)
def test_a_mixed_disjunction_is_not_read_whole(card_db, phrase):
    stream = Stream.of(phrase)
    parse_object_filter(stream)
    assert not stream.done


@pytest.mark.parametrize(
    "phrase",
    ["Goblin or Elf creature", "artifact or creature", "artifact, enchantment, or planeswalker",
     "Mountain or Forest", "target spell or nonland permanent"],
)
def test_a_disjunction_along_one_dimension_still_reads(card_db, phrase):
    stream = Stream.of(phrase)
    assert parse_object_filter(stream) is not None and stream.done


@pytest.mark.parametrize(
    "name",
    ["Harnesser of Storms", "Skullport Merchant", "Satsuki, the Living Lore", "Aether Meltdown",
     "Hulkbuster Armor", "Traveling Chocobo"],
)
def test_the_misread_cards_are_now_unread(card_db, name):
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
