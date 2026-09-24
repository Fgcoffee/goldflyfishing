"""Who owns an object, as opposed to who controls it (CR 108.3).

The two come apart constantly, and a card that names a zone means the owner:
a graveyard, a hand and a library belong to a player and have no controller
at all, so "exile target card from your graveyard" is an owner question.

The constraint existed on the filter and nothing checked it, so it failed
*open* - an owner filter matched every object, and "your graveyard" reached
everyone's. Failing open is the one direction this module promises never to
fail in: an over-broad match lets a spell hit something it should not, where
a missed match merely does less than it should.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import find, matches
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter

YOURS = ObjectFilter(owner=ControllerRelation.YOU)
THEIRS = ObjectFilter(owner=ControllerRelation.OPPONENT)
YOUR_GRAVEYARD = ObjectFilter(
    owner=ControllerRelation.YOU, zones=frozenset({Zone.GRAVEYARD})
)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _bury(board, name, owner):
    card = board.hand(name, controller=owner)
    return board.game.move_object(card, Zone.GRAVEYARD, to_player=owner)


# ---------------------------------------------------------------------------
# The constraint itself
# ---------------------------------------------------------------------------


def test_your_own_object_matches(board):
    mine = board.play("Grizzly Bears", controller=0)
    assert matches(board.game, mine, YOURS, controller=PlayerId(0))


def test_someone_elses_object_does_not(board):
    """The bug: this matched, so every owner filter was no filter at all."""
    theirs = board.play("Runeclaw Bear", controller=1)
    assert not matches(board.game, theirs, YOURS, controller=PlayerId(0))


def test_an_opponent_filter_is_the_mirror(board):
    mine = board.play("Grizzly Bears", controller=0)
    theirs = board.play("Runeclaw Bear", controller=1)
    assert matches(board.game, theirs, THEIRS, controller=PlayerId(0))
    assert not matches(board.game, mine, THEIRS, controller=PlayerId(0))


def test_a_filter_that_asks_nothing_still_matches_everything(board):
    """The control: only a filter that *asks* about the owner constrains it."""
    theirs = board.play("Runeclaw Bear", controller=1)
    assert matches(board.game, theirs, ObjectFilter(), controller=PlayerId(0))


# ---------------------------------------------------------------------------
# Why it matters: ownership survives a change of control
# ---------------------------------------------------------------------------


def test_owner_is_not_controller(board):
    """CR 108.3: stealing a permanent does not make it yours to own, so an
    owner filter and a controller filter answer differently about it."""
    stolen = board.play("Runeclaw Bear", controller=1)
    stolen.controller = PlayerId(0)

    assert matches(
        board.game,
        stolen,
        ObjectFilter(controller=ControllerRelation.YOU),
        controller=PlayerId(0),
    )
    assert not matches(board.game, stolen, YOURS, controller=PlayerId(0))


# ---------------------------------------------------------------------------
# The case this was found through: "your graveyard"
# ---------------------------------------------------------------------------


def test_your_graveyard_is_yours_alone(board):
    """A graveyard has an owner and no controller, so this is the only way to
    say "your graveyard" at all."""
    _bury(board, "Grizzly Bears", owner=0)
    _bury(board, "Runeclaw Bear", owner=1)

    found = find(board.game, YOUR_GRAVEYARD, controller=PlayerId(0))
    names = {board.game.characteristics(o).name for o in found}

    assert names == {"Grizzly Bears"}


def test_an_unowned_zone_filter_reaches_both(board):
    """The control, and what the bug made every owner filter do."""
    _bury(board, "Grizzly Bears", owner=0)
    _bury(board, "Runeclaw Bear", owner=1)

    found = find(
        board.game,
        ObjectFilter(zones=frozenset({Zone.GRAVEYARD})),
        controller=PlayerId(0),
    )
    names = {board.game.characteristics(o).name for o in found}

    assert names == {"Grizzly Bears", "Runeclaw Bear"}
