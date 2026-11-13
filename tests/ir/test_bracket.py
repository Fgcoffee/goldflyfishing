"""Bracket-estimate classification (mtgfish.ir.bracket), read from the IR."""

from __future__ import annotations

import pytest

from mtgfish.ir.bracket import TAGS, estimate
from mtgfish.ir.store import card_document


def _tags(card_db, name: str) -> list[str]:
    card = card_db.lookup(name)
    assert card is not None, name
    return card_document(card)["bracket"]


@pytest.mark.parametrize(
    "name, tag",
    [
        ("Demonic Tutor", "tutor"),
        ("Vampiric Tutor", "tutor"),  # to the top of the library still counts
        ("Goblin Matron", "tutor"),
        ("Armageddon", "mass_land_denial"),
        ("Ravages of War", "mass_land_denial"),
        ("Jokulhaups", "mass_land_denial"),  # lands among other permanents
        ("Death Cloud", "mass_land_denial"),  # X lands from each player
        ("Boil", "mass_land_denial"),  # all Islands: four or more lands a player
        ("Smothering Tithe", "game_changer"),
    ],
)
def test_tagged(card_db, name, tag):
    assert tag in _tags(card_db, name)


@pytest.mark.parametrize(
    "name",
    [
        "Cultivate",  # basic lands: ramp, not a tutor
        "Farseek",  # land subtypes only
        "Polluted Delta",
        "Wrath of God",  # creatures, not lands
        "Stone Rain",  # one target land
        "Lotus Field",  # you sacrifice your own lands
        "Sol Ring",
    ],
)
def test_untagged(card_db, name):
    assert _tags(card_db, name) == []


def test_tags_come_in_order(card_db):
    tags = _tags(card_db, "Demonic Tutor")
    assert tags == [t for t in TAGS if t in tags]


def _doc(name, *tags, count=1):
    return {"name": name, "bracket": list(tags), "count": count}


def test_estimate_core_by_default():
    result = estimate([_doc("Forest", count=30), _doc("Grizzly Bears")])
    assert result["bracket"] == 2
    assert "two-card combos are not checked" in result["caveats"]


def test_estimate_game_changers():
    three = [_doc(f"GC{i}", "game_changer") for i in range(3)]
    assert estimate(three)["bracket"] == 3
    assert estimate(three + [_doc("GC3", "game_changer")])["bracket"] == 4


def test_estimate_mass_land_denial_is_optimized():
    result = estimate([_doc("Armageddon", "mass_land_denial")])
    assert result["bracket"] == 4
    assert result["cards"]["mass_land_denial"] == ["Armageddon"]


def test_estimate_tutors_and_copies():
    assert estimate([_doc("Tutor", "tutor", count=3)])["bracket"] == 2
    assert estimate([_doc("Tutor", "tutor", count=4)])["bracket"] == 3


def test_estimate_says_unread_is_a_floor():
    result = estimate([_doc("Time Warp", "unread")])
    assert result["bracket"] == 2
    assert any("not fully read" in c for c in result["caveats"])
