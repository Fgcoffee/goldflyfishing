""""Two target ..." takes two targets, whoever chooses them (CR 601.2c,
115.1d): Wicked Pact and Ulamog, the Ceaseless Hunger from parsed text."""

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


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def offers(box, name):
    game = box.game
    return [a for a in legal_actions(game, P0)
            if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == name]


def resolve_all(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def test_wicked_pact_destroys_both_targets_by_default(box):
    need(box, "Wicked Pact", "Grizzly Bears", "Hill Giant")
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Hill Giant", "battlefield", 1)
    box.put("Wicked Pact", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 3)
    box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), 2)
    (action,) = offers(box, "Wicked Pact")
    assert _perform(box.game, P0, action)
    resolve_all(box)
    assert [o.zone for o in live(box, "Grizzly Bears")] == [Zone.GRAVEYARD]
    assert [o.zone for o in live(box, "Hill Giant")] == [Zone.GRAVEYARD]


def test_two_targets_are_needed_to_cast_it(box):
    need(box, "Wicked Pact", "Grizzly Bears")
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Wicked Pact", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 5)
    assert offers(box, "Wicked Pact") == []


def test_naming_one_target_for_two_is_refused(box):
    need(box, "Wicked Pact", "Grizzly Bears", "Hill Giant")
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Hill Giant", "battlefield", 1)
    box.put("Wicked Pact", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 5)
    (action,) = offers(box, "Wicked Pact")
    (bears,) = live(box, "Grizzly Bears")
    assert not _perform(box.game, P0, replace(action, targets=((bears.id,),)))


def test_ulamogs_cast_trigger_exiles_two(box):
    need(box, "Ulamog, the Ceaseless Hunger", "Grizzly Bears", "Hill Giant")
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Hill Giant", "battlefield", 1)
    box.put("Ulamog, the Ceaseless Hunger", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), 10)
    (action,) = offers(box, "Ulamog, the Ceaseless Hunger")
    assert _perform(box.game, P0, action)
    resolve_all(box)
    assert [o.zone for o in live(box, "Grizzly Bears")] == [Zone.EXILE]
    assert [o.zone for o in live(box, "Hill Giant")] == [Zone.EXILE]
