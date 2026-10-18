"""Twincast from its parsed oracle text: the copy may be aimed elsewhere.

Copies keep the original's targets unless the effect allows a new choice
(CR 707.10c), which the parser carries as the copy's MAY_CHOOSE_NEW_TARGETS
when the text says "You may choose new targets for the copy" (auditor's F23).
Without it, a Twincast copy of a Bolt hits the same Bear.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_cr707_copy_new_targets import Retargets, bolt_the_first_bear  # noqa: F401

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Lightning Bolt", "Grizzly Bears", "Twincast"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def _bears_alive(box) -> list[int]:
    return sorted(
        obj.id
        for obj in box.game.objects.values()
        if obj.is_live
        and obj.card is not None
        and obj.card.name == "Grizzly Bears"
        and obj.zone.name == "BATTLEFIELD"
    )


def test_a_twincast_copy_of_bolt_can_kill_the_other_bear(box):
    spell, first, second = bolt_the_first_bear(box)
    box.put("Twincast", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 2)
    box.game.agents[0] = Retargets(second)
    action = next(
        a
        for a in legal_actions(box.game, P0)
        if a.kind.name.startswith("CAST") and box.game.objects[a.source].card.name == "Twincast"
    )
    assert _perform(box.game, P0, replace(action, targets=((spell.id,),)))
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            break
        box.resolve_top()
        box.settle()
    assert _bears_alive(box) == [], "the original killed one Bear, the redirected copy the other"
