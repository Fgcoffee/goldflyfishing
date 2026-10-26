""""You may pay {N}. If you do, ..." does nothing when the cost is not paid
- declined, or unpayable (CR 603.9, 608.2d). Mentor of the Meek and Nim
Deathmantle from their parsed text."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


class SaysYes(PassiveOpponent):
    def choose_optional(self, game, player, effect):
        return True


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = SaysYes()
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


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


@pytest.mark.parametrize("mana", [0, 1])
def test_mentor_draws_only_when_paid(box, mana):
    need(box, "Mentor of the Meek", "Grizzly Bears", "Island")
    box.put("Island", "library", 0, count=5)
    box.put("Mentor of the Meek", "battlefield", 0)
    resolve_all(box)
    if mana:
        box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), mana)
    hand = len(box.game.player(P0).hand)
    box.put("Grizzly Bears", "battlefield", 0)
    resolve_all(box)
    assert len(box.game.player(P0).hand) == hand + mana


@pytest.mark.parametrize("mana", [0, 4])
def test_nim_deathmantle_returns_only_when_paid(box, mana):
    need(box, "Nim Deathmantle", "Grizzly Bears")
    box.put("Nim Deathmantle", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    if mana:
        box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), mana)
    (bears,) = live(box, "Grizzly Bears")
    destroy(box.game, bears)
    resolve_all(box)
    (bears,) = live(box, "Grizzly Bears")
    assert bears.zone is (Zone.BATTLEFIELD if mana else Zone.GRAVEYARD)


def test_a_declined_payment_does_nothing(box):
    need(box, "Nim Deathmantle", "Grizzly Bears")
    box.game.agents[0] = PassiveOpponent()
    box.put("Nim Deathmantle", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), 4)
    (bears,) = live(box, "Grizzly Bears")
    destroy(box.game, bears)
    resolve_all(box)
    (bears,) = live(box, "Grizzly Bears")
    assert bears.zone is Zone.GRAVEYARD
    assert box.game.player(P0).mana_pool.total == 4
