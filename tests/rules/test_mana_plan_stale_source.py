"""F61: a mana plan whose source can no longer be activated when its turn
comes - an earlier activation sacrificed it, or the effect giving it the
ability ended - rewinds the cast (CR 605.3b, 601.2h) instead of crashing on
an ability that is not there (the IndexError reported from the web beta)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError
from mtgfish.rules.cr600_spells_and_abilities.mana_plan import execute_plan, plan_payment
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    from mtgfish.parser.compile import OracleAbilities

    real = OracleAbilities()

    class Real(ScriptedAbilities):
        def abilities_for(self, card, face_index):
            return real.abilities_for(card, face_index)

    for name in ("Sol Ring", "Forest"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return make_board(card_db, Real())


def test_a_planned_source_that_has_left_rewinds_the_cast(board):
    ring = board.play("Sol Ring")
    board.play("Forest")
    plan = plan_payment(board.game, P0, ManaCost.parse("{3}"))
    assert plan and any(o.source == ring.id for o in plan)
    destroy(board.game, ring)  # gone before its activation comes round
    with pytest.raises(CastError):
        execute_plan(board.game, P0, plan)


def test_a_planned_source_that_lost_its_ability_rewinds_the_cast(board):
    ring = board.play("Sol Ring")
    board.play("Forest")
    plan = plan_payment(board.game, P0, ManaCost.parse("{3}"))
    # The source now has no abilities at all - "loses all abilities", or a
    # copy effect that lapsed - as the Treasure in the report did.
    board.game.ability_provider = type("NoAbilities", (), {
        "abilities_for": lambda self, card, face: (), "generation": 99})()
    board.game.invalidate_characteristics()
    assert not board.game.characteristics(ring).abilities
    with pytest.raises(CastError):
        execute_plan(board.game, P0, plan)


def test_an_untouched_plan_still_pays(board):
    board.play("Sol Ring")
    board.play("Forest")
    plan = plan_payment(board.game, P0, ManaCost.parse("{3}"))
    execute_plan(board.game, P0, plan)
    assert board.game.player(P0).mana_pool.total == 3
