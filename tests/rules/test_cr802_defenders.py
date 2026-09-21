"""What a creature is allowed to attack (CR 506.2, 802.1).

The engine checked at length whether a creature *could* attack, and not at all
whether the thing it was aimed at was open to attack. An agent naming its own
controller, a player who had already lost, or a random permanent was taken at
its word.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    _attack_is_permitted,
    _combat,
    declare_attackers,
)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


@pytest.fixture
def four(card_db):
    return make_board(card_db, ScriptedAbilities(), players=4)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def test_an_opponent_may_be_attacked(board):
    """CR 506.2, the ordinary case."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    assert _attack_is_permitted(board.game, attacker, 1)


def test_a_creature_may_not_attack_its_own_controller(board):
    """CR 506.2: what may be attacked is the *defending* player."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    assert not _attack_is_permitted(board.game, attacker, 0)


def test_a_player_who_has_left_the_game_may_not_be_attacked(board):
    """CR 800.4: they are gone, and are nobody's opponent any more."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    board.game.player(1).has_lost = True
    assert not _attack_is_permitted(board.game, attacker, 1)


def test_any_opponent_may_be_attacked_in_multiplayer(four):
    """CR 802.1 with CR 903.2: the attack multiple players option is on, so
    the choice is not limited to one particular opponent."""
    attacker = four.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    assert all(_attack_is_permitted(four.game, attacker, p) for p in (1, 2, 3))


def test_attacks_are_not_declared_against_an_illegal_defender(four):
    """The check has to hold at declaration, not only in the helper."""

    class Suicidal:
        def declare_attackers(self, game, player, candidates):
            return {candidates[0].id: 0}  # attacking itself

    attacker = four.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    four.game.agents[0] = Suicidal()
    four.game.active_player = 0

    declare_attackers(four.game)
    assert not _combat(four.game).attacking


# ---------------------------------------------------------------------------
# Permanents
# ---------------------------------------------------------------------------


def test_an_opponents_planeswalker_may_be_attacked(board):
    """CR 506.2: planeswalkers they control."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    walker = board.play("Chandra, Torch of Defiance", controller=1)
    assert _attack_is_permitted(board.game, attacker, AttackPermanent(walker.id))


def test_your_own_planeswalker_may_not_be_attacked(board):
    """The defending player must still be an opponent."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    walker = board.play("Chandra, Torch of Defiance", controller=0)
    assert not _attack_is_permitted(board.game, attacker, AttackPermanent(walker.id))


def test_an_ordinary_permanent_may_not_be_attacked(board):
    """CR 506.2 lists players, planeswalkers and battles. A creature is not on
    that list, however much an agent would like it to be."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    bystander = board.play("Grizzly Bears", controller=1)
    assert not _attack_is_permitted(board.game, attacker, AttackPermanent(bystander.id))


def test_a_permanent_that_is_gone_may_not_be_attacked(board):
    """Nothing to attack, so nothing is declared."""
    attacker = board.play("Serra Angel", controller=0)
    attacker.summoning_sick = False
    assert not _attack_is_permitted(board.game, attacker, AttackPermanent(99999))
