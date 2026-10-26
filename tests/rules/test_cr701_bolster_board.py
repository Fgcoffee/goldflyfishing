"""Bolster from real oracle text, on a board (CR 701.39a).

"Choose a creature you control with the least toughness or tied for least
toughness among creatures you control. Put N +1/+1 counters on that
creature." Built as counters on every creature you control.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[1] = PassiveOpponent()
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def resolve_all(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def counters(box, player=0):
    """+1/+1 counters on each of a player's creatures, by name."""
    out = {}
    for obj in box.game.permanents(PlayerId(player)):
        if obj.card is not None and box.game.characteristics(obj).is_creature:
            out.setdefault(obj.card.name, []).append(obj.counter_count("+1/+1"))
    return {name: sorted(values) for name, values in out.items()}


def test_elite_scaleguard_bolsters_the_least_toughness(box):
    need(box, "Elite Scaleguard", "Llanowar Elves", "Serra Angel")
    box.put("Serra Angel", "battlefield", 0)
    box.put("Llanowar Elves", "battlefield", 0)
    box.put("Llanowar Elves", "battlefield", 1)  # an opponent's is not yours
    box.put("Elite Scaleguard", "battlefield", 0)
    resolve_all(box)
    assert counters(box) == {
        "Serra Angel": [0],
        "Llanowar Elves": [2],
        "Elite Scaleguard": [0],
    }
    assert counters(box, 1) == {"Llanowar Elves": [0]}


def test_bolster_picks_one_of_a_tie(box):
    need(box, "Elite Scaleguard", "Llanowar Elves")
    box.put("Llanowar Elves", "battlefield", 0)
    box.put("Llanowar Elves", "battlefield", 0)
    box.put("Elite Scaleguard", "battlefield", 0)
    resolve_all(box)
    assert counters(box)["Llanowar Elves"] == [0, 2]


def test_anafenza_bolsters_on_each_creature_entering(box):
    """Anafenza, 2/2, bolsters 1 as another nontoken creature enters. The 1/1
    Elves are the least; then Anafenza, the Elves and the Bears are all 2/2,
    and one of the three - not all of them - gets the second counter."""
    need(box, "Anafenza, Kin-Tree Spirit", "Llanowar Elves", "Grizzly Bears")
    box.put("Anafenza, Kin-Tree Spirit", "battlefield", 0)
    box.put("Llanowar Elves", "battlefield", 0)
    resolve_all(box)
    assert counters(box) == {"Anafenza, Kin-Tree Spirit": [0], "Llanowar Elves": [1]}
    box.put("Grizzly Bears", "battlefield", 0)
    resolve_all(box)
    assert sum(sum(values) for values in counters(box).values()) == 2
