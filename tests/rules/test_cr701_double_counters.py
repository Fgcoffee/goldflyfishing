"""Doubling counters (CR 701.10e).

"Double the number of +1/+1 counters on ..." gives each object as many more
of those counters as it already has. It was unread: Mossborn Hydra, Kalonian
Hydra and Bristly Bill did nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _named(box, name):
    return [
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == name and obj.zone.name == "BATTLEFIELD"
    ]


def _drain(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            break
        box.resolve_top()


def test_mossborn_hydra_doubles_its_counters_when_a_land_enters(box):
    box.put("Mossborn Hydra", "battlefield", 0)
    _drain(box)
    hydra = _named(box, "Mossborn Hydra")[0]
    hydra.add_counters("+1/+1", 3 - hydra.counter_count("+1/+1"))
    box.put("Forest", "battlefield", 0)
    _drain(box)
    assert hydra.counter_count("+1/+1") == 6


def test_bristly_bill_doubles_each_creature_you_control(box):
    box.put("Bristly Bill, Spine Sower", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0, count=2)
    box.put("Grizzly Bears", "battlefield", 1)
    _drain(box)
    mine = [o for o in _named(box, "Grizzly Bears") if o.controller == 0]
    theirs = [o for o in _named(box, "Grizzly Bears") if o.controller != 0]
    mine[0].add_counters("+1/+1", 2)
    theirs[0].add_counters("+1/+1", 2)
    box.give_mana(10)
    offered = [a for a in box.legal() if "Bristly Bill" in a["description"] and "{3}" in a["description"]]
    if not offered:
        offered = [a for a in box.legal() if "Bristly Bill" in a["description"]]
    assert offered
    assert not box.perform(offered[-1]["index"]).get("error")
    _drain(box)
    assert mine[0].counter_count("+1/+1") == 4
    assert mine[1].counter_count("+1/+1") == 0
    assert theirs[0].counter_count("+1/+1") == 2


def test_each_kind_of_counter(card_db):
    from harness import ScriptedAbilities, make_board

    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
    from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
    from mtgfish.rules.kernel.ids import PlayerId
    from mtgfish.rules.kernel.query import ObjectFilter

    board = make_board(card_db, ScriptedAbilities())
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 2)
    bears.add_counters("charge", 1)
    execute(
        Resolution(game=board.game, source=bears.id, controller=PlayerId(0)),
        (Effect(EffectKind.DOUBLE_COUNTERS, targets=ObjectFilter(source_only=True)),),
    )
    assert bears.counter_count("+1/+1") == 4
    assert bears.counter_count("charge") == 2
