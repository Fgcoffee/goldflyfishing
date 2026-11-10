"""F55: "Target creature an opponent controls can't be blocked. Goad that
creature." - the goad means the creature the restriction was put on
(CR 608.2c, 701.15a). Sly Instigator, parsed."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind, _perform
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Sly Instigator", "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def test_the_restricted_creature_is_goaded(box):
    game = box.game
    box.put("Sly Instigator", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 1)
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 1)
    (instigator,) = live(box, "Sly Instigator")
    (bears,) = live(box, "Grizzly Bears")
    activate = next(a for a in legal_actions(game, P0)
                    if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == instigator.id)
    assert _perform(game, P0, replace(activate, targets=((bears.id,),)))
    box.settle()
    while game.stack:
        box.resolve_top()
    assert P0 in bears.goaded_by
