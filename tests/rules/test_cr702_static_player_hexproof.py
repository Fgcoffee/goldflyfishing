"""F53b: a permanent's static "You have shroud" (True Believer) and "You
have hexproof" (Aegis of the Gods) stop its controller being targeted
(CR 702.18a, 702.11b) for as long as the permanent is there (CR 611.3a)."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("True Believer", "Aegis of the Gods", "Lightning Bolt"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def bolt_targets(box, caster):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        _candidates_for,
        targeting_effects,
    )

    bolt = next(o for o in live(box, "Lightning Bolt") if o.owner == caster)
    (effect,) = targeting_effects(box.game, bolt)
    return _candidates_for(box.game, bolt, effect, caster)


def test_shroud_stops_everyone_while_the_permanent_is_there(box):
    box.put("Lightning Bolt", "hand", 1)
    box.put("Lightning Bolt", "hand", 0)
    box.put("True Believer", "battlefield", 0)
    assert player_target(P0) not in bolt_targets(box, P1)
    assert player_target(P0) not in bolt_targets(box, P0), "shroud: its controller too"
    (believer,) = live(box, "True Believer")
    destroy(box.game, believer)
    assert player_target(P0) in bolt_targets(box, P1), "gone with its source"


def test_hexproof_stops_only_opponents(box):
    box.put("Lightning Bolt", "hand", 1)
    box.put("Lightning Bolt", "hand", 0)
    box.put("Aegis of the Gods", "battlefield", 0)
    assert player_target(P0) not in bolt_targets(box, P1)
    assert player_target(P0) in bolt_targets(box, P0)
