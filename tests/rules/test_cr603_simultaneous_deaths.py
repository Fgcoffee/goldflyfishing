"""Permanents that leave the battlefield at the same time see each other leave
(CR 603.10a, 603.6e, 704.3).

A destroy-all or a round of state-based actions moves its permanents one after
another. Each one's look-back triggers must still find the others as they were
immediately before the event, so a Blood Artist that dies with four other
creatures triggers five times, not once.
"""

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
    for name in ("Blood Artist", "Grizzly Bears", "Damnation", "Pyroclasm", "Shock"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def cast(box, name, color, amount, target=None):
    box.game.player(P0).mana_pool.add(ManaKind(color), amount)
    action = next(
        action
        for action in legal_actions(box.game, P0)
        if action.kind.name.startswith("CAST") and box.game.objects[action.source].card.name == name
    )
    if target is not None:
        action = replace(action, targets=((target,),))
    assert _perform(box.game, P0, action)
    box.settle()
    box.resolve_top()  # The spell itself; state-based actions follow.
    box.settle()


def artist_triggers(box) -> int:
    """Triggered abilities on the stack whose source is a Blood Artist."""
    count = 0
    for object_id in box.game.stack:
        source = box.game.objects.get(box.game.objects[object_id].source)
        if source is not None and source.card is not None:
            count += source.card.name == "Blood Artist"
    return count


def live(box, name):
    return [
        obj
        for obj in box.game.objects.values()
        if obj.is_live
        and obj.zone is Zone.BATTLEFIELD
        and obj.card is not None
        and obj.card.name == name
    ]


def board(box, bears=4):
    box.put("Blood Artist", "battlefield", 0)
    for _ in range(bears):
        box.put("Grizzly Bears", "battlefield", 0)


def test_destroy_all_triggers_for_every_creature(box):
    """Damnation: Blood Artist sees itself and the four Bears die together."""
    board(box)
    box.put("Damnation", "hand", 0)
    cast(box, "Damnation", Color.BLACK, 4)
    assert not live(box, "Grizzly Bears")
    assert artist_triggers(box) == 5
    assert len(box.game.stack) == 5


def test_lethal_damage_deaths_trigger_for_every_creature(box):
    """Pyroclasm: the creatures die in one round of state-based actions."""
    board(box)
    box.put("Pyroclasm", "hand", 0)
    cast(box, "Pyroclasm", Color.RED, 2)
    assert not live(box, "Grizzly Bears")
    assert artist_triggers(box) == 5


def test_blood_artist_dying_alone_triggers_once(box):
    """Control: one death is one trigger."""
    board(box, bears=0)
    box.put("Pyroclasm", "hand", 0)
    cast(box, "Pyroclasm", Color.RED, 2)
    assert artist_triggers(box) == 1


def test_another_creature_dying_alone_triggers_once(box):
    """Control: Blood Artist survives and sees one Bear die."""
    board(box, bears=2)
    box.put("Shock", "hand", 0)
    (bear, _) = live(box, "Grizzly Bears")
    cast(box, "Shock", Color.RED, 1, target=bear.id)
    assert len(live(box, "Grizzly Bears")) == 1
    assert live(box, "Blood Artist")
    assert artist_triggers(box) == 1
