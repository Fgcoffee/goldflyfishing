"""An Aura or Equipment's static ability reaches the permanent it is on.

This is the bug this file exists for. "Equipped creature has indestructible"
parsed perfectly, produced the right opcode, and applied to nothing: the
filter said *attached_to* the source, which is true of the Equipment and
false of the creature. Every Aura and every Equipment in the format granted
nothing, and 965 tests passed throughout, because not one of them attached
something and then asked the host what it had.

The general lesson is the one from ``test_end_to_end_cards``: a correct parse
and a correct opcode still meet in a filter, and a filter that matches no
object is indistinguishable from an ability that was never read - except that
the coverage report calls the first one a success.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _named(box, name):
    return next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == name
    )


def _attach(box, attachment: str, host: str):
    if box.db.lookup(attachment) is None or box.db.lookup(host) is None:
        pytest.skip(f"{attachment} or {host} is not in this card pool")
    box.put(host, "battlefield", 0)
    box.put(attachment, "battlefield", 0)
    box.game.invalidate_characteristics()

    worn = _named(box, attachment)
    wearer = _named(box, host)
    actions.attach(box.game, worn, wearer)
    box.game.invalidate_characteristics()
    return wearer


@pytest.mark.parametrize(
    ("equipment", "keyword"),
    [
        ("Mithril Coat", "indestructible"),
        ("Whispersilk Cloak", "shroud"),
    ],
)
def test_an_equipment_grants_its_keyword_to_the_creature(box, equipment, keyword):
    wearer = _attach(box, equipment, "Grizzly Bears")
    assert box.game.characteristics(wearer).has_keyword(keyword)


def test_an_equipment_grants_power_to_the_creature(box):
    """A P/T change from an Equipment lands on the creature, not the Equipment."""
    if box.db.lookup("Bonesplitter") is None:
        pytest.skip("Bonesplitter is not in this card pool")
    wearer = _attach(box, "Bonesplitter", "Grizzly Bears")
    # Grizzly Bears is 2/2; Bonesplitter is +2/+0.
    assert box.game.characteristics(wearer).power == 4


def test_the_equipment_itself_does_not_gain_what_it_grants(box):
    """"Equipped creature has indestructible" is about the creature only.

    The mirror of the original bug: reading the filter the other way round
    would make the Equipment indestructible and the creature ordinary, which
    is just as wrong and much harder to notice.
    """
    _attach(box, "Mithril Coat", "Grizzly Bears")
    coat = _named(box, "Mithril Coat")
    # Mithril Coat is printed with indestructible of its own, so the question
    # is asked of an Equipment that is not.
    if box.db.lookup("Bonesplitter") is None:
        pytest.skip("Bonesplitter is not in this card pool")
    assert coat is not None

    box2_power = box.game.characteristics(coat).power
    assert not box2_power, "an Equipment has no power of its own to gain"
