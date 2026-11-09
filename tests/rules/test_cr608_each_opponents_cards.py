"""Untargeted "each opponent's" cards are only theirs: Soul-Guide Lantern's
"{T}, Sacrifice this artifact: Exile each opponent's graveyard" leaves your
own graveyard alone (CR 108.3, 608.2)."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind, _perform
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)
LANTERN = "Soul-Guide Lantern"


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in (LANTERN, "Grizzly Bears", "Hill Giant"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def test_exile_each_opponents_graveyard_spares_yours(box):
    game = box.game
    box.put(LANTERN, "battlefield", 0)
    # Its enters trigger finds no card to exile while the graveyards are empty.
    box.settle()
    while game.stack:
        box.resolve_top()
    box.put("Grizzly Bears", "graveyard", 0)
    box.put("Hill Giant", "graveyard", 1)
    (lantern,) = live(box, LANTERN)
    sacrifice = [
        a for a in legal_actions(game, P0)
        if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == lantern.id
        and "graveyard" in str(game.characteristics(lantern).abilities[a.ability_index].text)
        and "each opponent" in str(game.characteristics(lantern).abilities[a.ability_index].text)
    ]
    assert sacrifice, "the exile-graveyards ability is offered"
    assert _perform(game, P0, sacrifice[0])
    box.settle()
    while game.stack:
        box.resolve_top()
    (giant,) = live(box, "Hill Giant")
    (bears,) = live(box, "Grizzly Bears")
    assert giant.zone.name == "EXILE"
    assert bears.zone.name == "GRAVEYARD", "your own graveyard is not an opponent's"
