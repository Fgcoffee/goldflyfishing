"""Modal headers (CR 700.2): how many modes, and whether one may repeat.

The words before the dash are part of the ability. "Choose two", "choose one
or both", "choose up to one" and "choose any number" each allow a different
set of choices; "If this spell was kicked, choose any number instead" is a
second header for when its condition holds. A header the engine cannot
honour - "choose one that hasn't been chosen this turn", an upgrade on a
condition it does not read - leaves the ability unread rather than letting
it run as "choose one".
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind


def _modal(card_db, name):
    card = card_db.lookup(name)
    assert card is not None, name
    parsed = parse_card(card)
    for face in parsed.faces:
        for ability in face.abilities:
            for effect in ability.effects:
                for node in effect.walk():
                    if node.kind is EffectKind.CHOOSE_MODE and node.children:
                        return parsed, node
    return parsed, None


def _header(node):
    amount = node.amount.constant if node.amount.is_constant else None
    return (amount or 1, node.modes_up_to, node.modes_at_least, node.modes_may_repeat)


@pytest.mark.parametrize(
    ("name", "header"),
    [
        # exactly N
        ("Kolaghan's Command", (2, False, 0, False)),
        ("Mishra, Lost to Phyrexia", (3, False, 0, False)),
        # CR 700.2d: the licence to repeat is read, not assumed
        ("Mystic Confluence", (3, False, 0, True)),
        # a floor of one, a ceiling of all
        ("Farewell", (4, True, 1, False)),
        ("Blessed Alliance", (3, True, 1, False)),
        ("Scour for Scrap", (2, True, 1, False)),
        # a ceiling, no floor
        ("Hullbreaker Horror", (1, True, 0, False)),
        ("Rankle, Master of Pranks", (3, True, 0, False)),
        # and plain "choose one" stays what it was
        ("Charming Prince", (1, False, 0, False)),
    ],
)
def test_the_header_sets_the_choice(card_db, name, header):
    parsed, node = _modal(card_db, name)
    assert node is not None, [str(f) for f in parsed.failures]
    assert _header(node) == header


@pytest.mark.parametrize(
    "name",
    [
        # a per-turn memory of chosen modes
        "Monument to Endurance",
        # a reflexive trigger between the trigger and the header
        "Hylda of the Icy Crown",
        # a mode choice at random
        "Cult of Skaro",
        # a constraint across modes
        "Vindictive Lich",
    ],
)
def test_a_header_the_engine_cannot_honour_is_not_read(card_db, name):
    """Rather than running as "choose one", which it does not say."""
    card = card_db.lookup(name)
    assert card is not None, name
    parsed = parse_card(card)
    assert not parsed.fully_parsed
    assert any(f.rule == "modal" or "modal" in str(f) for f in parsed.failures)


def test_a_kicker_upgrade_is_read(card_db):
    """"Choose one. If this spell was kicked, choose any number instead." """
    from mtgfish.rules.kernel.query import ConditionKind

    parsed, node = _modal(card_db, "Inscription of Abundance")
    assert node is not None, [str(f) for f in parsed.failures]
    assert _header(node) == (1, False, 0, False)
    condition, budget, up_to, at_least = node.modes_instead
    assert condition.kind is ConditionKind.WAS_KICKED
    assert (budget, up_to, at_least) == (3, True, 0)


def test_you_may_choose_both_instead_keeps_one_as_the_floor(card_db):
    parsed, node = _modal(card_db, "Jeska's Will")
    assert node is not None, [str(f) for f in parsed.failures]
    condition, budget, up_to, at_least = node.modes_instead
    assert condition.filter is not None and condition.filter.is_commander
    assert (budget, up_to, at_least) == (2, True, 1)


@pytest.mark.parametrize(
    "name",
    [
        # the condition reader turns "an artifact and an enchantment" into "or"
        "Soul Transfer",
        # "it was kicked" asked from a cast trigger
        "Depth Defiler",
        # conditions the upgrade grammar does not take
        "Flame of Anor",
        "Molten Collapse",
    ],
)
def test_an_upgrade_on_an_unread_condition_is_not_read(card_db, name):
    card = card_db.lookup(name)
    assert card is not None, name
    assert not parse_card(card).fully_parsed
