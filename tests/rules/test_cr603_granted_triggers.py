"""A triggered ability granted by an effect triggers like a printed one
(CR 613.1f, 603.10a)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
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
    for name in ("Abnormal Endurance", "Shock", "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def bears(game) -> list:
    return [
        obj
        for obj in game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == "Grizzly Bears"
    ]


def cast(box, name, target):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind

    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 2)
    box.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    action = next(
        action
        for action in legal_actions(box.game, P0)
        if action.kind.name.startswith("CAST")
        and box.game.objects[action.source].card.name == name
    )
    assert _perform(box.game, P0, replace(action, targets=((target,),)))
    resolve_everything(box)


def resolve_everything(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def test_a_granted_dies_trigger_returns_the_creature(box):
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Abnormal Endurance", "hand", 0)
    box.put("Shock", "hand", 0)
    (creature,) = bears(box.game)
    cast(box, "Abnormal Endurance", creature.id)
    (creature,) = bears(box.game)
    cast(box, "Shock", creature.id)
    (returned,) = bears(box.game)
    assert returned.zone is Zone.BATTLEFIELD
    assert returned.id != creature.id
    assert returned.tapped


def test_without_the_grant_the_creature_stays_dead(box):
    """Control: Shock alone kills the Bears and nothing brings them back."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Shock", "hand", 0)
    (creature,) = bears(box.game)
    cast(box, "Shock", creature.id)
    (dead,) = bears(box.game)
    assert dead.zone is Zone.GRAVEYARD
