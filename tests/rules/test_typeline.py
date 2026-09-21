"""Type line parsing (CR 205)."""

from __future__ import annotations

import pytest

from mtgfish.rules.cr205_typeline import SubtypeRegistry, TypeLine, TypeLineError
from mtgfish.rules.enums import CardType, Supertype


@pytest.fixture
def registry() -> SubtypeRegistry:
    reg = SubtypeRegistry()
    reg.register(CardType.CREATURE, ["Human", "Wizard", "Time Lord", "Doctor", "Insect"])
    reg.register(CardType.LAND, ["Urza's", "Mine", "Forest", "Locus"])
    reg.register(CardType.ENCHANTMENT, ["Aura", "Saga", "Background"])
    reg.register(CardType.ARTIFACT, ["Equipment", "Vehicle"])
    return reg


def test_basic(registry):
    line = TypeLine.parse("Legendary Creature — Human Wizard", registry)
    assert line.supertypes == Supertype.LEGENDARY
    assert line.types == CardType.CREATURE
    assert line.subtypes == ("Human", "Wizard")


def test_no_subtypes(registry):
    line = TypeLine.parse("Artifact", registry)
    assert line.types == CardType.ARTIFACT
    assert line.subtypes == ()


def test_multiple_types(registry):
    line = TypeLine.parse("Artifact Creature — Construct", registry)
    assert line.has_type(CardType.ARTIFACT)
    assert line.has_type(CardType.CREATURE)


def test_multiword_subtype_stays_whole(registry):
    """"Time Lord" is one creature type, not two.

    Splitting on spaces would invent a "Time" creature type and a "Lord" one,
    and every tribal effect downstream would be wrong.
    """
    line = TypeLine.parse("Legendary Creature — Time Lord Doctor", registry)
    assert line.subtypes == ("Time Lord", "Doctor")


def test_adjacent_single_word_subtypes_stay_split(registry):
    """Urza's Mine really is two land types, unlike Time Lord."""
    line = TypeLine.parse("Land — Urza's Mine", registry)
    assert line.subtypes == ("Urza's", "Mine")


def test_unknown_subtype_is_kept_not_dropped(registry):
    """A new set's subtype should show up in the type line, not vanish."""
    line = TypeLine.parse("Creature — Gloomhollow", registry)
    assert line.subtypes == ("Gloomhollow",)


def test_unknown_type_word_raises_by_default(registry):
    with pytest.raises(TypeLineError):
        TypeLine.parse("Eaturecray — Wolf", registry)


def test_scan_reports_unknown_words_without_raising(registry):
    line, unknown = TypeLine.scan("Scariest Creature — Wolf", registry)
    assert unknown == ("Scariest",)
    assert line.has_type(CardType.CREATURE)


def test_legacy_summon_maps_to_creature(registry):
    assert TypeLine.parse("Summon — Wizard", registry).has_type(CardType.CREATURE)


def test_creature_types_filtered_by_registry(registry):
    line = TypeLine.parse("Enchantment Creature — Aura Human", registry)
    assert line.creature_types(registry) == ("Human",)


def test_layer_four_additions_are_additive(registry):
    """CR 205.1b: adding a type does not remove existing ones."""
    line = TypeLine.parse("Land", registry)
    animated = line.adding(types=CardType.CREATURE, subtypes=("Elemental",))
    assert animated.has_type(CardType.LAND)
    assert animated.has_type(CardType.CREATURE)
    assert animated.subtypes == ("Elemental",)


def test_removal(registry):
    line = TypeLine.parse("Artifact Creature — Construct", registry)
    stripped = line.removing(types=CardType.CREATURE, subtypes=("Construct",))
    assert stripped.types == CardType.ARTIFACT
    assert stripped.subtypes == ()


def test_roundtrip_str(registry):
    text = "Legendary Artifact Creature — Equipment Wizard"
    assert str(TypeLine.parse(text, registry)) == text


def test_permanent_detection(registry):
    assert TypeLine.parse("Creature — Human", registry).is_permanent_type
    assert not TypeLine.parse("Instant", registry).is_permanent_type
