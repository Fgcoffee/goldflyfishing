"""Basic landcycling from its real oracle text, on a board (CR 702.29e).

"Search your library for a basic land card": one card. The search already
stopped at one, because the effect's amount was 1, but the ability's filter
had no count - "every basic land" - and anything reading the ability rather
than running it (the explanation, the spot check) was told it found them all.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.explain import explain_ability
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[1] = PassiveOpponent()
    for name in ("Sylvan Reclamation", "Forest", "Grizzly Bears"):
        if table.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def landcycling(box):
    (ability,) = [
        a for a in box.provider.abilities_for(box.db.lookup("Sylvan Reclamation"), 0)
        if a.keyword == "Basic landcycling"
    ]
    return ability


def test_basic_landcycling_finds_one_basic_land(box):
    box.put("Forest", "library", 0, count=5)
    box.put("Grizzly Bears", "library", 0, count=2)
    box.put("Sylvan Reclamation", "hand", 0)
    box.give_mana(2)
    (action,) = [a for a in box.legal(0) if a["kind"] == "ACTIVATE_ABILITY"]
    box.perform(action["index"])
    while box.game.stack:
        box.resolve_top()
    game = box.game
    hand = [game.objects[i].card.name for i in game.player(P0).hand]
    assert hand == ["Forest"]
    assert len(game.player(P0).library) == 6
    assert [game.objects[i].card.name for i in game.player(P0).graveyard] == [
        "Sylvan Reclamation"
    ]


def test_basic_landcycling_says_one_card(box):
    (search,) = landcycling(box).effects
    assert search.targets.count is not None
    assert "all basic land" not in explain_ability(landcycling(box))
