"""A planeswalker's entering loyalty counters are counters put on it
(CR 306.5b, 122.6, 614.1c), so Doubling Season doubles them."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    for name in ("Liliana Vess", "Doubling Season"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def cast_liliana(box) -> int:
    """Cast Liliana Vess and return the loyalty counters she entered with."""
    box.put("Liliana Vess", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 5)
    action = next(
        action
        for action in legal_actions(box.game, P0)
        if action.kind.name.startswith("CAST")
        and box.game.objects[action.source].card.name == "Liliana Vess"
    )
    assert _perform(box.game, P0, action)
    box.settle()
    box.resolve_top()
    box.settle()
    (liliana,) = (
        obj
        for obj in box.game.objects.values()
        if obj.is_live
        and obj.zone is Zone.BATTLEFIELD
        and obj.card is not None
        and obj.card.name == "Liliana Vess"
    )
    return liliana.counter_count("loyalty")


def test_doubling_season_doubles_entering_loyalty(box):
    box.put("Doubling Season", "battlefield", 0)
    assert cast_liliana(box) == 10


def test_without_doubling_season_she_enters_with_printed_loyalty(box):
    """Control: CR 306.5b alone gives her printed loyalty."""
    assert cast_liliana(box) == 5
