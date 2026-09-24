"""Combat damage division (CR 510.1).

The rules used to make an attacker order its blockers and assign lethal damage
to each before moving to the next. That requirement is gone: CR 510.1c lets the
controller divide the damage however they like. Only trample (CR 702.19b) still
cares about lethal damage, and only as a condition on spilling over.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    _attacker_assignment,
    _blocker_assignment,
    _combat,
    lethal_damage,
)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


class Divider:
    """An agent with a division in mind."""

    def __init__(self, division):
        self.division = division

    def assign_combat_damage(self, game, player, source, recipients, total):
        return self.division


def _amounts(assignments):
    return {who.id if hasattr(who, "id") else who: amount
            for _, who, amount, _, _ in assignments}


# ---------------------------------------------------------------------------
# CR 510.1c: a blocked creature divides freely
# ---------------------------------------------------------------------------


def test_an_attacker_may_split_its_damage_between_blockers(board):
    """CR 510.1c: a 4/4 may put 2 on each of two blockers it could not kill
    in order. Under the old ordering rule this was illegal."""
    attacker = board.play("Serra Angel", controller=0)  # 4/4
    one = board.play("Grizzly Bears", controller=1)  # 2/2
    two = board.play("Runeclaw Bear", controller=1)  # 2/2

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id, two.id]
    board.game.active_player = 0
    board.game.agents[0] = Divider({0: 2, 1: 2})

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {
        one.id: 2,
        two.id: 2,
    }


def test_an_attacker_may_dump_everything_on_one_blocker(board):
    """CR 510.1c: all 4 on the second blocker, ignoring the first entirely."""
    attacker = board.play("Serra Angel", controller=0)
    one = board.play("Grizzly Bears", controller=1)
    two = board.play("Runeclaw Bear", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id, two.id]
    board.game.active_player = 0
    board.game.agents[0] = Divider({1: 4})

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {two.id: 4}


def test_a_division_that_does_not_spend_every_point_is_refused(board):
    """CR 510.1e: the total is checked, and an illegal assignment does not
    stand. The engine falls back to the legal default instead."""
    attacker = board.play("Serra Angel", controller=0)
    one = board.play("Grizzly Bears", controller=1)
    two = board.play("Runeclaw Bear", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id, two.id]
    board.game.active_player = 0
    board.game.agents[0] = Divider({0: 1})  # only 1 of 4 points

    amounts = _amounts(_attacker_assignment(board.game, combat, attacker))
    assert sum(amounts.values()) == 4


def test_a_division_aimed_at_a_stranger_is_refused(board):
    """CR 510.1c: the damage goes to the creatures blocking it, nowhere else."""
    attacker = board.play("Serra Angel", controller=0)
    one = board.play("Grizzly Bears", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id]
    board.game.active_player = 0
    board.game.agents[0] = Divider({7: 4})

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {one.id: 4}


def test_the_default_is_lethal_in_turn(board):
    """No agent opinion: a deterministic, legal division."""
    attacker = board.play("Serra Angel", controller=0)
    one = board.play("Grizzly Bears", controller=1)
    two = board.play("Runeclaw Bear", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id, two.id]
    board.game.active_player = 0

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {
        one.id: 2,
        two.id: 2,
    }


# ---------------------------------------------------------------------------
# CR 702.19b: trample is the one place lethal damage still binds
# ---------------------------------------------------------------------------


def test_trample_may_not_spill_before_blockers_have_lethal(board):
    """CR 702.19b: the controller need not assign lethal to the blocker, but
    then cannot assign any damage to the player."""
    board.scripts.add("Serra Angel", keyword("Trample"))
    attacker = board.play("Serra Angel", controller=0)  # 4/4 trample
    blocker = board.play("Grizzly Bears", controller=1)  # 2/2

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [blocker.id]
    board.game.active_player = 0
    # 1 to the blocker, 3 to the player: illegal, the blocker lacks lethal.
    board.game.agents[0] = Divider({0: 1, 1: 3})

    amounts = _amounts(_attacker_assignment(board.game, combat, attacker))
    assert amounts.get(blocker.id, 0) >= 2, amounts


def test_trample_spills_once_the_blocker_has_lethal(board):
    """CR 702.19b: 2 to the 2/2, the other 2 through to the player."""
    board.scripts.add("Serra Angel", keyword("Trample"))
    attacker = board.play("Serra Angel", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [blocker.id]
    board.game.active_player = 0

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {
        blocker.id: 2,
        1: 2,
    }


def test_a_trampler_may_still_over_assign_to_the_blocker(board):
    """CR 510.1c: putting everything on the blocker is a legal division."""
    board.scripts.add("Serra Angel", keyword("Trample"))
    attacker = board.play("Serra Angel", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [blocker.id]
    board.game.active_player = 0
    board.game.agents[0] = Divider({0: 4})

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {
        blocker.id: 4
    }


def test_deathtouch_makes_one_damage_lethal_for_trample(board):
    """CR 702.2b with CR 702.19b: the classic 1 to the blocker, rest through."""
    board.scripts.add("Serra Angel", keyword("Trample"), keyword("Deathtouch"))
    attacker = board.play("Serra Angel", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [blocker.id]
    board.game.active_player = 0

    assert _amounts(_attacker_assignment(board.game, combat, attacker)) == {
        blocker.id: 1,
        1: 3,
    }


def test_damage_already_marked_lowers_what_lethal_means(board):
    """CR 702.19b: marked damage counts toward lethal."""
    blocker = board.play("Grizzly Bears", controller=1)  # 2/2
    assert lethal_damage(board.game, blocker) == 2
    blocker.damage = 1
    assert lethal_damage(board.game, blocker) == 1


def test_damage_being_assigned_this_step_counts_too(board):
    """CR 702.19b: another creature's assignment in the same step counts, which
    is what lets two attackers share a blocker and both trample over."""
    blocker = board.play("Grizzly Bears", controller=1)
    assert lethal_damage(board.game, blocker, marked={blocker.id: 1}) == 1


# ---------------------------------------------------------------------------
# CR 510.1d: a blocker blocking several attackers
# ---------------------------------------------------------------------------


def test_a_blocker_divides_among_the_creatures_it_blocks(board):
    """CR 510.1d, and no lethal-first requirement here either."""
    blocker = board.play("Serra Angel", controller=1)  # 4/4
    one = board.play("Grizzly Bears", controller=0)
    two = board.play("Runeclaw Bear", controller=0)

    combat = _combat(board.game)
    combat.attacking[one.id] = 1
    combat.attacking[two.id] = 1
    combat.blocking[blocker.id] = [one.id, two.id]
    board.game.agents[1] = Divider({0: 1, 1: 3})

    assert _amounts(_blocker_assignment(board.game, combat, blocker)) == {
        one.id: 1,
        two.id: 3,
    }


def test_a_blocker_blocking_one_creature_assigns_it_everything(board):
    """CR 510.1d: no choice to make."""
    blocker = board.play("Serra Angel", controller=1)
    one = board.play("Grizzly Bears", controller=0)

    combat = _combat(board.game)
    combat.attacking[one.id] = 1
    combat.blocking[blocker.id] = [one.id]

    assert _amounts(_blocker_assignment(board.game, combat, blocker)) == {one.id: 4}


def test_a_blocker_whose_attackers_are_gone_assigns_nothing(board):
    """CR 510.1d: blocking nothing means assigning nothing."""
    blocker = board.play("Serra Angel", controller=1)

    combat = _combat(board.game)
    combat.blocking[blocker.id] = []

    assert _blocker_assignment(board.game, combat, blocker) == []
