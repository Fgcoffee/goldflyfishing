"""F56: Force of Will's alternative cost - pay 1 life and exile a blue card
from your hand - is offered with a spell to counter and another blue card in
hand, and paying it counters the spell (CR 118.9, 601.2b). Without another
blue card it is not offered: the card cannot pay for itself."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Force of Will", "Brainstorm", "Lightning Bolt"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def bolt_on_the_stack(box):
    game = box.game
    box.put("Lightning Bolt", "hand", 1)
    game.player(P1).mana_pool.add(ManaKind(Color.RED), 1)
    (bolt,) = live(box, "Lightning Bolt")
    cast = next(a for a in legal_actions(game, P1) if a.source == bolt.id)
    assert _perform(game, P1, replace(cast, targets=((player_target(P0),),)))


def alternative_casts(box):
    (fow,) = live(box, "Force of Will")
    return [a for a in legal_actions(box.game, P0)
            if a.source == fow.id and a.alternative_cost >= 0]


def test_the_alternative_cost_is_offered_and_counters(box):
    game = box.game
    box.put("Force of Will", "hand", 0)
    box.put("Brainstorm", "hand", 0)
    bolt_on_the_stack(box)
    (cast,) = alternative_casts(box)
    life = game.player(P0).life
    assert _perform(game, P0, cast)
    assert game.player(P0).life == life - 1
    (brainstorm,) = live(box, "Brainstorm")
    assert brainstorm.zone is Zone.EXILE
    while game.stack:
        box.resolve_top()
    (bolt,) = live(box, "Lightning Bolt")
    assert bolt.zone is Zone.GRAVEYARD
    assert game.player(P0).life == life - 1, "the Bolt was countered"


def test_it_cannot_exile_itself(box):
    box.put("Force of Will", "hand", 0)
    bolt_on_the_stack(box)
    assert not alternative_casts(box)
