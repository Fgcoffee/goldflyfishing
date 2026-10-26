"""Keywords granted to "spells you cast", from the cards' parsed text, work
during that cast (CR 601.2a-b): convoke (Chief Engineer), improvise
(Inspiring Statuary) and cascade (The First Sliver)."""

from __future__ import annotations

from dataclasses import replace

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
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def grant_is_read(box, name):
    """The parser may refuse a grant until it has checked it; then there is
    nothing for the engine to do yet."""
    from mtgfish.parser.compile import parse_card

    abilities = [a for f in parse_card(box.db.lookup(name)).faces for a in f.abilities]
    if not abilities or any(a.unparsed for a in abilities):
        pytest.skip(f"the parser does not read {name}'s grant yet")


def live(box, name):
    return [
        o for o in box.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == name
    ]


def cast(box, name):
    game = box.game
    action = next(
        (
            a
            for a in legal_actions(game, P0)
            if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == name
        ),
        None,
    )
    assert action is not None, f"{name} is not offered"
    assert _perform(game, P0, replace(action))


def test_granted_convoke_pays_with_creatures(box):
    need(box, "Chief Engineer", "Mind Stone", "Grizzly Bears")
    grant_is_read(box, "Chief Engineer")
    box.put("Chief Engineer", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Mind Stone", "hand", 0)
    cast(box, "Mind Stone")  # {2}: the Engineer and the Bears tap for it
    box.settle()
    assert box.game.stack, "Mind Stone is on the stack"
    assert all(o.tapped for o in live(box, "Grizzly Bears") + live(box, "Chief Engineer"))


def test_granted_improvise_pays_with_an_artifact(box):
    need(box, "Inspiring Statuary", "Grizzly Bears", "Forest")
    grant_is_read(box, "Inspiring Statuary")
    box.put("Inspiring Statuary", "battlefield", 0)
    box.put("Grizzly Bears", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.GREEN), 1)
    cast(box, "Grizzly Bears")  # {1}{G}: the Statuary taps for the {1}
    (statuary,) = live(box, "Inspiring Statuary")
    assert statuary.tapped


def test_granted_cascade_triggers(box):
    need(box, "The First Sliver", "Sliver Hivelord")
    grant_is_read(box, "The First Sliver")
    box.put("The First Sliver", "battlefield", 0)
    box.put("Grizzly Bears", "library", 0, count=3)
    box.put("Sliver Hivelord", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.WHITE), 1)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 1)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 1)
    box.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    box.game.player(P0).mana_pool.add(ManaKind(Color.GREEN), 1)
    cast(box, "Sliver Hivelord")
    box.settle()
    texts = " ".join(
        str(getattr(box.game.objects[oid], "ability", "") or "") for oid in box.game.stack
    ).lower()
    assert len(box.game.stack) >= 2 and "cascade" in texts


def test_granted_conspire_is_offered(box):
    need(box, "Wort, the Raidmother", "Goblin Piker", "Lightning Bolt")
    grant_is_read(box, "Wort, the Raidmother")
    box.put("Wort, the Raidmother", "battlefield", 0)
    box.put("Goblin Piker", "battlefield", 0, count=2)
    box.put("Lightning Bolt", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    game = box.game
    offered = sorted(
        a.additional_costs
        for a in legal_actions(game, P0)
        if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == "Lightning Bolt"
    )
    assert offered == [(), (0,)]
