"""Granted keywords (CR 613.1f, layer 6): what a grant actually hands out.

Grouped by construct: protection qualities, keywords the registry expands,
keywords the engine cannot run, and "loses" naming keywords.
"""

from __future__ import annotations

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import Color


def _read(text: str):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    return effects if stream.done else None


def _grants(effects):
    return [
        ability
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.GRANT_ABILITY
        for ability in node.granted_abilities
    ]


# -- protection keeps its quality (CR 702.16a, 702.16g) ----------------------


def test_protection_from_two_colours_is_two_abilities_with_qualities():
    effects = _read("Equipped creature gets +2/+2 and has protection from red and from blue.")
    granted = _grants(effects)
    assert [a.keyword for a in granted] == ["Protection", "Protection"]
    assert [a.quality.colors_any for a in granted] == [Color.RED, Color.BLUE]
    # A quality is asked of a source anywhere - a spell on the stack too.
    assert all(not a.quality.zones for a in granted)


def test_protection_from_everything_has_no_quality():
    (ability,) = _grants(_read("You gain protection from everything until your next turn."))
    assert ability.keyword == "Protection" and ability.quality is None


def test_protection_from_a_choice_is_not_read():
    assert _read(
        "Target creature you control gains protection from the color of your choice until end of turn."
    ) is None
    assert _read("Creatures you control gain protection from the chosen color until end of turn.") is None


def test_protection_keeps_the_duration_after_it():
    effects = _read("Target creature gains protection from artifacts until end of turn.")
    (grant,) = [n for e in effects for n in e.walk() if n.kind is EffectKind.GRANT_ABILITY]
    assert grant.duration != 0


# -- keywords the registry expands ---------------------------------------------


def test_granted_triggered_keyword_is_the_real_trigger():
    (ability,) = _grants(_read("Artifact creatures you control have afflict 3."))
    assert ability.kind is AbilityKind.TRIGGERED and ability.keyword.lower() == "afflict"


def test_granted_cost_keyword_keeps_its_cost(card_db):
    from mtgfish.parser import parse_card

    abilities = parse_card(card_db.lookup("Astor, Bearer of Blades")).faces[0].abilities
    granted = [a for ab in abilities for a in _grants(ab.effects)]
    (ability,) = [a for a in granted if a.keyword.lower() == "equip"]
    assert ability.kind is AbilityKind.ACTIVATED and "{1}" in str(ability.cost)


def test_granted_ward_is_the_real_ward_trigger():
    """CR 702.21a: a granted ward is the triggered ability ward is, with its
    cost - not an inert ability named Ward."""
    effects = _read("Other creatures you control have ward {2}.")
    assert effects is not None
    (ability,) = _grants(effects)
    assert ability.kind is AbilityKind.TRIGGERED
    assert not any(node.is_unparsed for effect in ability.effects for node in effect.walk())


def test_a_combat_keyword_the_engine_cannot_run_is_not_granted():
    """Myriad makes attacking token copies (CR 702.116a). Until it has a
    builder, granting it must fail rather than grant a stand-in."""
    assert _read("Equipped creature has myriad.") is None


# -- losing named keywords -------------------------------------------------------


def test_loses_a_choice_of_keywords_is_not_read():
    assert _read("Target creature loses your choice of flying, first strike, or trample until end of turn.") is None
    assert _read("Target creature loses first strike or swampwalk until end of turn.") is None


def test_loses_names_the_keywords():
    effects = _read("Permanents your opponents control lose hexproof and indestructible until end of turn.")
    (node,) = [n for e in effects for n in e.walk() if n.kind is EffectKind.REMOVE_ABILITIES]
    assert set(k.lower() for k in node.keywords) == {"hexproof", "indestructible"}
