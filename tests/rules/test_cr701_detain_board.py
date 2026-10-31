"""Detain from real oracle text, on a board (CR 701.35a).

New Prahv Guildmage: "{3}{W}{U}: Detain target nonland permanent an
opponent controls." Until the detaining player's next turn, that permanent
can't attack or block and its activated abilities can't be activated. Built
with no duration, the restrictions never ended - and with the target's
filter as their subject, they reached every permanent the filter described.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr500_turn_structure.restrictions import Act, prohibited
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[1] = PassiveOpponent()
    for name in ("New Prahv Guildmage", "Grizzly Bears", "Llanowar Elves"):
        if table.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def one(box, name, player):
    (obj,) = [o for o in box.game.permanents(PlayerId(player))
              if o.card is not None and o.card.name == name]
    return obj


def detain(box, target):
    box.put("New Prahv Guildmage", "battlefield", 0)
    guildmage = one(box, "New Prahv Guildmage", 0)
    box.give_mana(5)
    (action,) = [
        a for a in box.legal(0)
        if a["source"] == guildmage.id and "etain" in a["description"]
    ]
    box.perform(action["index"], targets=[[target.id]])
    box.settle()
    while box.game.stack:
        box.resolve_top()


def detained(box, obj) -> bool:
    game = box.game
    return all(
        prohibited(game, act, obj=obj) is not None
        for act in (Act.ATTACK, Act.BLOCK, Act.ACTIVATE_ABILITY)
    )


def test_detain_holds_the_target(box):
    box.put("Grizzly Bears", "battlefield", 1)
    bears = one(box, "Grizzly Bears", 1)
    detain(box, bears)
    assert detained(box, bears)


def test_detain_holds_only_the_target(box):
    """Control: the opponent's other creature, and your own, are free."""
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Llanowar Elves", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 0)
    bears = one(box, "Grizzly Bears", 1)
    detain(box, bears)
    game = box.game
    elves = one(box, "Llanowar Elves", 1)
    mine = one(box, "Grizzly Bears", 0)
    for free in (elves, mine):
        assert prohibited(game, Act.ATTACK, obj=free) is None
        assert prohibited(game, Act.BLOCK, obj=free) is None


def test_detain_lasts_until_your_next_turn(box):
    box.put("Grizzly Bears", "battlefield", 1)
    bears = one(box, "Grizzly Bears", 1)
    detain(box, bears)
    game = box.game
    # Your turn ends; the opponent's whole turn is played (the bench runs
    # a turn for whoever is active, then hands the turn on).
    game.active_player = P1
    box.next_turn()
    assert game.active_player == P0
    assert detained(box, bears), "still detained through the opponent's turn"
    # CR 701.35a: it ends as your next turn begins.
    box.next_turn()
    assert not any(
        prohibited(game, act, obj=bears) is not None
        for act in (Act.ATTACK, Act.BLOCK, Act.ACTIVATE_ABILITY)
    )
