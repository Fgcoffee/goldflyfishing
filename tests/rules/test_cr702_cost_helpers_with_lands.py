"""Convoke, improvise and delve pay part of a cost while lands on the
battlefield pay the rest (CR 702.51a, 702.126a, 702.66a), and an unmade
payment leaves nothing behind (CR 601.2h)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Zone
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
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def live(box, name):
    return [
        o for o in box.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == name
    ]


def cast(box, name):
    game = box.game
    action = next(
        (
            a for a in legal_actions(game, P0)
            if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == name
        ),
        None,
    )
    assert action is not None, f"{name} is not offered"
    return _perform(game, P0, replace(action))


def test_convoke_with_forests(box):
    need(box, "Siege Wurm", "Forest", "Grizzly Bears")
    box.put("Forest", "battlefield", 0, count=4)
    box.put("Grizzly Bears", "battlefield", 0, count=3)
    box.put("Siege Wurm", "hand", 0)  # {5}{G}{G}
    assert cast(box, "Siege Wurm")
    assert [o.zone for o in live(box, "Siege Wurm")] == [Zone.STACK]
    assert all(o.tapped for o in live(box, "Forest") + live(box, "Grizzly Bears"))


def test_improvise_with_islands(box):
    need(box, "Reverse Engineer", "Island", "Ornithopter")
    box.put("Island", "battlefield", 0, count=2)
    box.put("Ornithopter", "battlefield", 0, count=3)
    box.put("Reverse Engineer", "hand", 0)  # {3}{U}{U}
    assert cast(box, "Reverse Engineer")
    assert [o.zone for o in live(box, "Reverse Engineer")] == [Zone.STACK]


def test_delve_with_an_island(box):
    need(box, "Treasure Cruise", "Island", "Grizzly Bears")
    box.put("Island", "battlefield", 0)
    box.put("Grizzly Bears", "graveyard", 0, count=7)
    box.put("Treasure Cruise", "hand", 0)  # {7}{U}
    assert cast(box, "Treasure Cruise")
    assert [o.zone for o in live(box, "Treasure Cruise")] == [Zone.STACK]
    assert len([o for o in live(box, "Grizzly Bears") if o.zone is Zone.EXILE]) == 7


def test_only_the_helpers_needed_are_used(box):
    """Convoke a {5}{G}{G} with four Forests: three creatures, not more."""
    need(box, "Siege Wurm", "Forest", "Grizzly Bears")
    box.put("Forest", "battlefield", 0, count=4)
    box.put("Grizzly Bears", "battlefield", 0, count=5)
    box.put("Siege Wurm", "hand", 0)
    assert cast(box, "Siege Wurm")
    assert sum(o.tapped for o in live(box, "Grizzly Bears")) == 3


def test_an_unmade_payment_puts_delved_cards_back(card_db):
    """CR 601.2h: the rewind returns cards exiled for delve to the graveyard."""
    from harness import ScriptedAbilities, make_board

    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        _mana_checkpoint,
        _restore_mana_checkpoint,
    )

    board = make_board(card_db, ScriptedAbilities())
    card = board.graveyard("Grizzly Bears")
    checkpoint = _mana_checkpoint(board.game, P0)
    board.game.move_object(card, Zone.EXILE)
    _restore_mana_checkpoint(board.game, P0, checkpoint)
    zones = [o.zone for o in board.game.objects.values()
             if o.is_live and o.card is not None and o.card.name == "Grizzly Bears"]
    assert zones == [Zone.GRAVEYARD]


def test_convoke_with_creatures_alone_pays_coloured_pips(box):
    """CR 702.51a: a green creature pays for {G}. Seven Bears, no lands."""
    need(box, "Siege Wurm", "Grizzly Bears")
    box.put("Grizzly Bears", "battlefield", 0, count=7)
    box.put("Siege Wurm", "hand", 0)
    assert cast(box, "Siege Wurm")
    assert [o.zone for o in live(box, "Siege Wurm")] == [Zone.STACK]


def test_convoke_with_creatures_and_a_land_for_a_coloured_pip(box):
    """Six Bears and one Forest pay {5}{G}{G}: a Bear covers one {G}."""
    need(box, "Siege Wurm", "Forest", "Grizzly Bears")
    box.put("Forest", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0, count=6)
    box.put("Siege Wurm", "hand", 0)
    assert cast(box, "Siege Wurm")
    assert [o.zone for o in live(box, "Siege Wurm")] == [Zone.STACK]
