"""Modular and Fading from their real oracle text, on a board.

Both were built inert after the auditing session found them wrong: Modular
counted the counters on the card in the graveyard, so Arcbound Worker moved
none (CR 702.43a, 608.2h); Fading sacrificed as the last counter came off,
an upkeep early (CR 702.32a).
"""

from __future__ import annotations

from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def board_for(card_db):
    board = make_board(card_db, OracleAbilities())
    board.game.active_player = P0
    return board


def test_modular_and_fading_cards_are_read(card_db):
    for name in ("Arcbound Worker", "Arcbound Ravager", "Blastoderm"):
        assert parse_card(card_db.lookup(name)).fully_parsed, name


def test_modular_moves_the_counters_it_had_on_the_battlefield(card_db):
    """CR 702.43a with 608.2h: the counters on the permanent as it last
    existed - the card in the graveyard has none."""
    board = board_for(card_db)
    worker = board.play("Arcbound Worker")
    worker.add_counters("+1/+1", 2)
    thopter = board.play("Ornithopter")
    destroy(board.game, worker)
    board.settle()
    board.resolve_stack()
    assert thopter.counter_count("+1/+1") == 2


def test_modular_with_no_counters_moves_none(card_db):
    board = board_for(card_db)
    worker = board.play("Arcbound Worker")
    thopter = board.play("Ornithopter")
    destroy(board.game, worker)
    board.settle()
    board.resolve_stack()
    assert thopter.counter_count("+1/+1") == 0


def _upkeep(board):
    board.game.emit(Event(EventKind.UPKEEP, player=P0))
    board.settle()
    board.resolve_stack()
    board.sba()


def test_fading_sacrifices_only_when_no_counter_can_be_removed(card_db):
    """CR 702.32a: Blastoderm, fading 3, loses a counter on each of three
    upkeeps and survives them at 0; the fourth upkeep finds none to remove
    and sacrifices it."""
    board = board_for(card_db)
    blastoderm = board.play("Blastoderm")
    blastoderm.add_counters("fade", 3)
    for remaining in (2, 1, 0):
        _upkeep(board)
        assert "Blastoderm" in board.alive(0)
        assert blastoderm.counter_count("fade") == remaining
    _upkeep(board)
    assert "Blastoderm" not in board.alive(0)
    assert "Blastoderm" in board.in_graveyard(0)
