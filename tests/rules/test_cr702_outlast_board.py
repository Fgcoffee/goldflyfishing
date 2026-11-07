"""Outlast from its real oracle text, on a board.

CR 702.107a: "[Cost], {T}: Put a +1/+1 counter on this creature. Activate
only as a sorcery." Built without the {T}, Salt Road Patrol could grow as
many times a turn as there was mana.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[1] = PassiveOpponent()
    if table.db.lookup("Salt Road Patrol") is None:
        pytest.skip("Salt Road Patrol is not in this card pool")
    table.put("Salt Road Patrol", "battlefield", 0)
    return table


def patrol(box):
    (obj,) = [o for o in box.game.objects.values()
              if o.is_live and o.card is not None and o.card.name == "Salt Road Patrol"]
    return obj


def outlast_actions(box):
    return [a for a in box.legal(0)
            if a["kind"] == "ACTIVATE_ABILITY" and a["name"] == "Salt Road Patrol"]


def test_outlast_taps_and_grows_once(box):
    box.give_mana(5)
    (action,) = outlast_actions(box)
    box.perform(action["index"])
    while box.game.stack:
        box.resolve_top()
    obj = patrol(box)
    assert obj.counter_count("+1/+1") == 1
    assert obj.tapped
    # CR 702.107a: the {T} is the limit - plenty of mana, no second use.
    assert outlast_actions(box) == []


def test_outlast_needs_an_untapped_creature(box):
    box.give_mana(5)
    patrol(box).tapped = True
    assert outlast_actions(box) == []


def test_outlast_is_sorcery_speed(box):
    """Control: untapped and with mana, but not on the opponent's turn."""
    box.give_mana(5)
    assert outlast_actions(box)
    box.game.active_player = P1
    assert outlast_actions(box) == []
