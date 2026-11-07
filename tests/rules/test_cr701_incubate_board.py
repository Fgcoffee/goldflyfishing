"""Incubate from real oracle text, on a board (CR 701.53a, 111.10i).

Sculpted Perfection: "When this enchantment enters, incubate 2." The
Incubator was built as a bare 0/0 artifact with no counters and no way to
transform, so incubating did nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[1] = PassiveOpponent()
    if table.db.lookup("Sculpted Perfection") is None:
        pytest.skip("Sculpted Perfection is not in this card pool")
    return table


def resolve_all(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()
    box.settle()


def incubator(box):
    """The one token on P0's battlefield."""
    (token,) = [o for o in box.game.permanents(P0)
                if "Incubator" in box.game.characteristics(o).subtypes]
    return token


def incubate(box):
    box.put("Sculpted Perfection", "battlefield", 0)
    resolve_all(box)
    return incubator(box)


def test_incubate_makes_an_incubator_with_counters(box):
    token = incubate(box)
    chars = box.game.characteristics(token)
    assert token.counter_count("+1/+1") == 2
    assert "Incubator" in chars.subtypes
    # The front face is an artifact, not a creature: it does not die as a
    # 0/0 and Sculpted Perfection's anthem does not reach it.
    assert chars.types & CardType.ARTIFACT
    assert not chars.is_creature
    assert token.is_live


def test_the_incubator_transforms_into_a_phyrexian(box):
    token = incubate(box)
    box.give_mana(2)
    (action,) = [a for a in box.legal(0)
                 if a["kind"] == "ACTIVATE_ABILITY" and a["source"] == token.id]
    box.perform(action["index"])
    resolve_all(box)
    chars = box.game.characteristics(token)
    assert token.is_live
    assert chars.name == "Phyrexian Token"
    assert chars.is_creature and chars.types & CardType.ARTIFACT
    assert "Phyrexian" in chars.subtypes
    # CR 712.18: the same object, counters kept - 0/0, two counters, and
    # Sculpted Perfection's +1/+1 for Phyrexians.
    assert token.counter_count("+1/+1") == 2
    assert (chars.power, chars.toughness) == (3, 3)
    # The back face has no transform ability of its own.
    assert not [a for a in box.legal(0)
                if a["kind"] == "ACTIVATE_ABILITY" and a["source"] == token.id]


def test_no_transform_without_the_mana(box):
    """Control: the {2} is a real cost."""
    token = incubate(box)
    assert not [a for a in box.legal(0)
                if a["kind"] == "ACTIVATE_ABILITY" and a["source"] == token.id]
    assert not box.game.characteristics(token).is_creature
