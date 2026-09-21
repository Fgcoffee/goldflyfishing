"""The exile zone (CR 406).

Exile is the one zone with a rule about moving *within* it: CR 406.7 says an
already-exiled object that becomes exiled becomes a new object without
changing zones. Both halves matter, and they pull in opposite directions.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import exile
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.events import EventKind


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _watch(board):
    seen: list[EventKind] = []
    board.game.observer = lambda game, event: seen.append(event.kind)
    return seen


def test_exiling_from_the_battlefield_changes_zones(board):
    """The control: an ordinary trip to exile is a zone change (CR 400.7)."""
    obj = board.play("Grizzly Bears", controller=0)
    seen = _watch(board)
    exile(board.game, obj)
    assert EventKind.ZONE_CHANGE in seen
    assert EventKind.LEAVES_BATTLEFIELD in seen


def test_re_exiling_makes_a_new_object(board):
    """CR 406.7: it becomes a new object that has just been exiled."""
    obj = board.play("Grizzly Bears", controller=0)
    first = exile(board.game, obj)
    second = exile(board.game, first)
    assert second.id != first.id


def test_re_exiling_is_not_a_zone_change(board):
    """CR 406.7: it does not change zones, so nothing may observe one.

    The object is already in exile and stays there. An engine that emits a
    zone change here is telling every zone-change watcher something that did
    not happen.
    """
    obj = board.play("Grizzly Bears", controller=0)
    first = exile(board.game, obj)
    seen = _watch(board)
    exile(board.game, first)
    assert EventKind.ZONE_CHANGE not in seen


def test_re_exiling_still_counts_as_being_exiled(board):
    """CR 406.7: the new object has *just been exiled*, so that much is seen."""
    obj = board.play("Grizzly Bears", controller=0)
    first = exile(board.game, obj)
    seen = _watch(board)
    exile(board.game, first)
    assert EventKind.EXILED in seen


def test_only_the_new_object_is_in_exile_afterwards(board):
    """One card, one object in the zone - not two."""
    obj = board.play("Grizzly Bears", controller=0)
    first = exile(board.game, obj)
    second = exile(board.game, first)
    assert board.game.zone_list(Zone.EXILE, 0) == [second.id]


def test_the_superseded_object_is_no_longer_live(board):
    """CR 400.7's bookkeeping still applies within the zone."""
    obj = board.play("Grizzly Bears", controller=0)
    first = exile(board.game, obj)
    second = exile(board.game, first)
    assert first.superseded_by == second.id
    assert second.previous_id == first.id
