"""The combat keywords that change power and toughness as blocks are declared.

Each is built by ``cr702_keyword_impl`` from its CR 702 definition and played
out on a real board: the declaration fires the trigger, the stack resolves
it, and the test reads the creatures' power and toughness afterwards - so a
keyword that triggers on the wrong thing, or does more than its rule says,
shows up as a wrong number.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    declare_attackers,
    declare_blockers,
)
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def kw(name: str, **kwargs) -> tuple:
    return build(KeywordInstance(name, **kwargs))


def _combat(board, attackers: dict, blockers: dict) -> None:
    """Declare attackers and blockers, then resolve whatever triggered."""
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = FixedAgent(attackers=attackers)
    game.agents[PlayerId(1)] = FixedAgent(blockers=blockers)
    declare_attackers(game)
    board.settle()
    board.resolve_stack()
    declare_blockers(game)
    board.settle()
    board.resolve_stack()
    board.refresh()


# ---------------------------------------------------------------------------
# Bushido (CR 702.45a)
# ---------------------------------------------------------------------------


def test_bushido_pumps_an_attacker_that_becomes_blocked(board):
    board.scripts.add("Grizzly Bears", *kw("Bushido", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(bears) == (4, 4)
    assert board.pt(giant) == (3, 3)


def test_bushido_pumps_a_blocker(board):
    board.scripts.add("Hill Giant", *kw("Bushido", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(giant) == (4, 4)
    assert board.pt(bears) == (2, 2)


def test_bushido_does_nothing_for_an_unblocked_attacker(board):
    board.scripts.add("Grizzly Bears", *kw("Bushido", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _combat(board, {bears.id: 1}, {})
    assert board.pt(bears) == (2, 2)


# ---------------------------------------------------------------------------
# Rampage (CR 702.23a-b)
# ---------------------------------------------------------------------------


def test_rampage_counts_blockers_beyond_the_first(board):
    board.scripts.add("Hill Giant", *kw("Rampage", amount=2))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    third = board.play("Grizzly Bears", controller=1)
    _combat(
        board,
        {giant.id: 1},
        {first.id: [giant.id], second.id: [giant.id], third.id: [giant.id]},
    )
    # Three blockers: two beyond the first, +2/+2 for each.
    assert board.pt(giant) == (7, 7)


def test_rampage_with_a_single_blocker_gives_nothing(board):
    board.scripts.add("Hill Giant", *kw("Rampage", amount=2))
    giant = board.play("Hill Giant", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {bears.id: [giant.id]})
    assert board.pt(giant) == (3, 3)


def test_rampage_counts_the_blockers_left_when_it_resolves(board):
    """CR 702.23b: calculated once, as the ability resolves."""
    board.scripts.add("Hill Giant", *kw("Rampage", amount=1))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={giant.id: 1})
    game.agents[PlayerId(1)] = FixedAgent(
        blockers={first.id: [giant.id], second.id: [giant.id]}
    )
    declare_attackers(game)
    declare_blockers(game)
    board.settle()
    # One blocker leaves combat with the trigger still on the stack.
    game.combat.remove(second.id)
    board.resolve_stack()
    board.refresh()
    assert board.pt(giant) == (3, 3)


# ---------------------------------------------------------------------------
# Flanking (CR 702.25a)
# ---------------------------------------------------------------------------


def test_flanking_shrinks_each_blocker_without_flanking(board):
    board.scripts.add("Hill Giant", *kw("Flanking"))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {first.id: [giant.id], second.id: [giant.id]})
    assert board.pt(first) == (1, 1)
    assert board.pt(second) == (1, 1)
    # The flanker itself is untouched.
    assert board.pt(giant) == (3, 3)


def test_flanking_ignores_a_blocker_with_flanking(board):
    board.scripts.add("Hill Giant", *kw("Flanking"))
    board.scripts.add("Grizzly Bears", *kw("Flanking"))
    giant = board.play("Hill Giant", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {bears.id: [giant.id]})
    assert board.pt(bears) == (2, 2)


def test_flanking_does_nothing_on_a_blocker(board):
    """It triggers on *this* creature becoming blocked, not on it blocking."""
    board.scripts.add("Hill Giant", *kw("Flanking"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(bears) == (2, 2)
    assert board.pt(giant) == (3, 3)
