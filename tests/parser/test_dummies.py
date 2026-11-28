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


def test_engine_implemented_matches_the_browser_engine():
    """dummies.ENGINE_IMPLEMENTED is engine/src/rules/dummies.ts's IMPLEMENTED_DUMMIES."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "engine/src/rules/dummies.ts").read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if "IMPLEMENTED_DUMMIES" in l and "new Set" in l)
    assert set(re.findall(r'"([a-z0-9-]+)"', line)) == set(dummies.ENGINE_IMPLEMENTED)
    assert dummies.ENGINE_IMPLEMENTED <= {d.name for d in dummies.DUMMIES}


@pytest.mark.parametrize("name, how, source", [
    ("Exotic Orchard", "could-produce", "land an opponent controls"),
    ("Reflecting Pool", "could-produce-type", "land you control"),
    ("Chrome Mox", "exiled-card", None),
    ("Mox Amber", "colours-among", "legendary creature or planeswalker you control"),
    ("Bloom Tender", "one-of-each-colour-among", "object you control"),
])
def test_a_colour_limited_mana_ability_tracks_its_limit(card_db, name, how, source):
    """The auditor's choice (a): the mana stays any colour (no game changes),
    and the limit is a tracked dummy carrying what limits it, for the rules
    session to switch on."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup(name)).faces[0]
    mana = next(a for a in face.abilities if any(e.kind is EffectKind.ADD_MANA for e in a.effects))
    limit = mana.effects[-1]
    assert limit.kind is EffectKind.DUMMY and limit.rule == "mana-colour-limit"
    assert limit.keywords == (how,)
    if source is None:
        assert limit.targets is None
    else:
        assert source in limit.targets.describe()


def test_an_unlimited_colour_choice_has_no_limit(card_db):
    from mtgfish.parser.compile import parse_card

    for name in ("Birds of Paradise", "Command Tower"):
        face = parse_card(card_db.lookup(name)).faces[0]
        assert not [n for a in face.abilities for e in a.effects for n in e.walk() if n.kind is EffectKind.DUMMY]
