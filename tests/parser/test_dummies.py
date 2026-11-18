"""Dummy abilities: recognised shapes the engine can't do yet (parser/dummies.py)."""

from __future__ import annotations

import pytest

from mtgfish.parser import dummies
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind


def _dummy_rules(ability):
    return [node.rule for effect in ability.effects for node in effect.walk() if node.kind is EffectKind.DUMMY]


@pytest.mark.parametrize("name, kind", [
    ("Seedborn Muse", "untap-during-others-untap-step"),
    ("Leyline of Anticipation", "begin-game-on-battlefield"),
    ("Mistrise Village", "next-spell-rider"),
])
def test_a_whole_ability_is_recognised(card_db, name, kind):
    from mtgfish.parser.compile import parse_card

    parsed = parse_card(card_db.lookup(name))
    assert parsed.fully_parsed
    rules = [r for face in parsed.faces for a in face.abilities for r in _dummy_rules(a)]
    assert rules == [kind]


def test_a_rider_keeps_the_part_that_reads(card_db):
    """Path of Ancestry still taps for mana; only the scry rider is a dummy."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup("Path of Ancestry")).faces[0]
    mana = next(a for a in face.abilities if a.is_mana_ability)
    assert [e.kind for e in mana.effects] == [EffectKind.ADD_MANA, EffectKind.DUMMY]
    assert mana.effects[-1].rule == "mana-spent-trigger"


def test_patterns_match_whole_text_only():
    assert dummies.whole("Untap all permanents you control during each other player's untap step.")
    # A longer ability that only contains the words is not the shape.
    assert dummies.whole(
        "Untap all permanents you control during each other player's untap step. Draw a card."
    ) is None
    assert dummies.rider("When that mana is spent to cast a creature spell, scry 1.") is None  # nothing before it


def test_every_dummy_names_what_the_engine_needs():
    names = [d.name for d in dummies.DUMMIES]
    assert len(names) == len(set(names))
    assert all(d.needs and (d.whole or d.rider or d.grammar) for d in dummies.DUMMIES)


def test_a_whole_dummy_on_an_activated_ability_keeps_its_cost(card_db):
    """Mistrise Village: "{U}, {T}: The next spell ... can't be countered" is
    still activated, with its cost, and the dummy as its effect."""
    from mtgfish.parser.compile import parse_card
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind

    face = parse_card(card_db.lookup("Mistrise Village")).faces[0]
    rider = next(a for a in face.abilities if _dummy_rules(a))
    assert rider.kind is AbilityKind.ACTIVATED
    assert len(rider.cost.components) == 2


def test_this_ability_costs_less_is_not_an_effect_on_resolution(card_db):
    """Boseiju (readings_queue.md): the reduction was the ability's last
    effect, so it ran after the cost was paid and cheapened the card's cast.
    Now it's a recognised dummy carrying its amount; the ability works at full
    cost."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup("Boseiju, Who Endures")).faces[0]
    channel = next(a for a in face.abilities if "less to activate" in a.text)
    kinds = [n.kind for e in channel.effects for n in e.walk()]
    assert EffectKind.MODIFY_COST not in kinds
    marker = channel.effects[-1]
    assert marker.kind is EffectKind.DUMMY and marker.rule == "activation-cost-change"
    assert marker.amount.operands[0].constant == -1
