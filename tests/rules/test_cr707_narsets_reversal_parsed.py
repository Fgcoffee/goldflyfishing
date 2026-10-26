"""Narset's Reversal from its parsed oracle text: the rider sits after another
instruction, and the copy may still be aimed elsewhere (CR 707.10c).

"Copy target instant or sorcery spell, then return it to its owner's hand.
You may choose new targets for the copy."
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_cr707_copy_new_targets import Retargets, bolt_the_first_bear  # noqa: F401
from test_cr707_twincast_parsed import _bears_alive, box  # noqa: F401

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


@pytest.mark.xfail(
    strict=True,
    reason="the parser refuses this ability (a 'choose new targets' rider after another "
    "instruction), and with the refusal switched off the engine's change-targets path "
    "left the copy on the first Bear (checked on rules 06453f8); remove this marker "
    "when both are fixed",
)
def test_narsets_reversal_copies_aims_elsewhere_and_returns_the_original(box):
    if box.db.lookup("Narset's Reversal") is None:
        pytest.skip("Narset's Reversal is not in this card pool")
    spell, first, second = bolt_the_first_bear(box)
    box.put("Narset's Reversal", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 2)
    box.game.agents[0] = Retargets(second)
    action = next(
        a
        for a in legal_actions(box.game, P0)
        if a.kind.name.startswith("CAST")
        and box.game.objects[a.source].card.name == "Narset's Reversal"
    )
    assert _perform(box.game, P0, replace(action, targets=((spell.id,),)))
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            break
        box.resolve_top()
        box.settle()
    assert _bears_alive(box) == [first], "the copy killed the other Bear; the Bolt went back to hand"
