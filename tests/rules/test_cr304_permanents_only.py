"""Only a permanent card can become a permanent (CR 304.4, 307.4).

An instant or sorcery that would enter the battlefield remains in its previous
zone instead. The rule matters because nothing else would clean up after it:
the state-based actions have no rule that removes an instant from the
battlefield, because the rule is that it never arrives.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr700_additional_rules.cr704_sba import check_state_based_actions
from mtgfish.rules.kernel.enums import Zone


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def test_an_instant_does_not_enter_the_battlefield(board):
    """CR 304.4."""
    card = board.hand("Lightning Bolt", controller=0)
    result = board.game.move_object(card, Zone.BATTLEFIELD)

    assert result.id not in board.game.battlefield


def test_an_instant_stays_in_its_previous_zone(board):
    """CR 304.4: "it remains in its previous zone instead" - it does not go to
    a graveyard, and it does not cease to exist."""
    card = board.hand("Lightning Bolt", controller=0)
    board.game.move_object(card, Zone.BATTLEFIELD)

    assert card.id in board.game.zone_list(Zone.HAND, 0)


def test_the_refused_move_returns_the_same_object(board):
    """No zone change happened, so CR 400.7 makes no new object."""
    card = board.hand("Lightning Bolt", controller=0)
    result = board.game.move_object(card, Zone.BATTLEFIELD)

    assert result is card


def test_a_sorcery_does_not_enter_the_battlefield(board):
    """CR 307.4, the same rule for the other spell type."""
    card = board.hand("Rampant Growth", controller=0)
    result = board.game.move_object(card, Zone.BATTLEFIELD)

    assert result.id not in board.game.battlefield
    assert card.id in board.game.zone_list(Zone.HAND, 0)


def test_state_based_actions_would_not_have_saved_us(board):
    """Why the rule has to refuse the move rather than clean up after it."""
    card = board.hand("Lightning Bolt", controller=0)
    board.game.move_object(card, Zone.BATTLEFIELD)
    check_state_based_actions(board.game)

    assert not any(
        board.game.objects[i].card is card.card for i in board.game.battlefield
    )


def test_a_creature_still_enters_the_battlefield(board):
    """The control: the rule names instants and sorceries, nothing else."""
    card = board.hand("Grizzly Bears", controller=0)
    result = board.game.move_object(card, Zone.BATTLEFIELD)

    assert result.id in board.game.battlefield


def test_a_land_still_enters_the_battlefield(board):
    """The other control - lands are permanents too, and are the common case
    for "put onto the battlefield" from a zone that is not the stack."""
    card = board.hand("Forest", controller=0)
    result = board.game.move_object(card, Zone.BATTLEFIELD)

    assert result.id in board.game.battlefield
