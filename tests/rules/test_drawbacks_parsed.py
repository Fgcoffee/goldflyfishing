"""Drawbacks read, so a card is not stronger than printed (auditor's F49).

The 500-card spot check found cards whose unread part was a restriction or
a cost: left out, the card played better than the real one. Each is read
now, or the card stays honestly unread.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import play_land
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def test_vivid_marsh_enters_tapped_with_two_charge_counters(card_db):
    assert parse_card(card_db.lookup("Vivid Marsh")).fully_parsed
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    land = board.hand("Vivid Marsh")
    assert play_land(game, P0, Action(ActionKind.PLAY_LAND, source=land.id))
    marsh = game.objects[land.superseded_by] if land.superseded_by else land
    assert marsh.tapped
    assert marsh.counters.get("charge") == 2


def _impostor_enters(card_db, *others):
    from mtgfish.rules.kernel.events import Event, EventKind

    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    for name in others:
        board.play(name)
    impostor = board.play("Faerie Impostor")
    game.emit(Event(EventKind.ENTERS_BATTLEFIELD, object_id=impostor.id, player=P0))
    board.settle()
    board.resolve_stack()
    board.settle()
    return board


def test_faerie_impostor_is_sacrificed_with_nothing_to_return(card_db):
    board = _impostor_enters(card_db)
    assert "Faerie Impostor" not in board.alive(0)


@pytest.mark.xfail(
    strict=True,
    reason="engine: can_pay_cost answers False for a RETURN_TO_HAND cost in an"
    " 'unless' payment, and pay_cost returns True without returning anything"
    " - reported to the rules session",
)
def test_faerie_impostor_stays_by_returning_another_creature(card_db):
    board = _impostor_enters(card_db, "Grizzly Bears")
    alive = board.alive(0)
    assert "Faerie Impostor" in alive
    assert "Grizzly Bears" not in alive
