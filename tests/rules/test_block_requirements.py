"""Blocking requirements (CR 509.1c) and the empty combat phase (CR 508.8).

The engine already refused illegal blocks (CR 509.1b). What it had no notion
of was a block that is *compulsory*: "all creatures able to block this creature
do so", "this creature must be blocked if able", "this creature blocks each
combat if able".

CR 509.1c does not simply say those creatures block. It says the declaration is
illegal unless it obeys the greatest number of requirements that any legal
declaration could obey - so the interesting cases are the ones where a
requirement and a restriction pull against each other, and the defending player
has to find the combination that satisfies the most.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    can_block_at_all,
    declare_attackers,
    declare_blockers,
)
from mtgfish.rules.cr500_turn_structure.restrictions import (
    BLOCKED_BY_ALL_ABLE,
    BLOCKS_IF_ABLE,
    MUST_BE_BLOCKED,
    Act,
    Restriction,
    block_obligations,
    enforce_block_requirements,
    register_standing,
)
from mtgfish.rules.kernel.enums import Step
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities(
        {
            "Serra Angel": (keyword("Flying"), keyword("Vigilance")),
            "Giant Spider": (keyword("Reach"),),
            "Goblin War Drums": (keyword("Menace"),),
        }
    )


def combat_board(card_db, scripts, *, attackers=None, blockers=None):
    board = make_board(card_db, scripts)
    board.game.agents[PlayerId(0)] = FixedAgent(attackers=attackers or {})
    board.game.agents[PlayerId(1)] = FixedAgent(blockers=blockers or {})
    board.game.active_player = PlayerId(0)
    return board


def attack_with(board, *attackers):
    """Declare every named creature as an attacker against player 1."""
    board.game.agents[PlayerId(0)].attackers = {obj.id: 1 for obj in attackers}
    declare_attackers(board.game)
    return board.game.combat


def enforce(board, combat, proposal):
    """Run CR 509.1b-c over a proposed declaration, as combat's hook will."""
    defender = PlayerId(1)
    candidates = [
        obj
        for obj in board.game.permanents(defender)
        if can_block_at_all(board.game, obj)
    ]
    return enforce_block_requirements(
        board.game, combat, defender, candidates, proposal
    )


def blocks(result, blocker):
    return list(result.get(blocker.id, ()))


# ---------------------------------------------------------------------------
# The declaration is left alone when nothing compels it
# ---------------------------------------------------------------------------


def test_a_board_with_no_requirements_passes_through_untouched(card_db, scripts):
    """The overwhelmingly common case: no requirement, no maximisation."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    combat = attack_with(board, bears)

    proposal = {giant.id: [bears.id]}
    assert enforce(board, combat, proposal) == proposal
    assert enforce(board, combat, {}) == {}


def test_a_creature_that_must_block_is_not_forced_onto_an_attacker_it_cannot_block(
    card_db, scripts
):
    """A requirement never beats a restriction (CR 509.1c)."""
    board = combat_board(card_db, scripts)
    angel = board.play("Serra Angel", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, angel)

    # CR 702.9b: the giant has neither flying nor reach, so there is no legal
    # declaration in which it blocks at all.
    assert enforce(board, combat, {}) == {}
    assert not blocks(enforce(board, combat, {}), giant)


# ---------------------------------------------------------------------------
# "This creature blocks each combat if able"
# ---------------------------------------------------------------------------


def test_a_creature_that_blocks_if_able_is_forced_into_the_block(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    result = enforce(board, combat, {})
    assert blocks(result, giant) == [bears.id]


def test_a_forced_blocker_keeps_the_attacker_its_controller_chose(card_db, scripts):
    """Which attacker it blocks is still the defending player's choice."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    wurm = board.play("Craw Wurm", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, bears, wurm)

    result = enforce(board, combat, {giant.id: [wurm.id]})
    assert blocks(result, giant) == [wurm.id]


def test_a_tapped_creature_is_under_no_blocking_requirement(card_db, scripts):
    """CR 509.1a: it was never able to block, so no requirement reaches it."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    board.play("Hill Giant", controller=1, tapped=True)
    board.refresh()
    combat = attack_with(board, bears)

    assert enforce(board, combat, {}) == {}


def test_a_cant_block_restriction_beats_the_requirement(card_db, scripts):
    """CR 101.2 and CR 509.1c: restrictions are checked first and are absolute."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    register_standing(
        board.game,
        Restriction(act=Act.BLOCK, subject=None, source=giant.id, text="can't block"),
    )
    combat = attack_with(board, bears)

    assert enforce(board, combat, {}) == {}


# ---------------------------------------------------------------------------
# "This creature must be blocked if able"
# ---------------------------------------------------------------------------


def test_an_attacker_that_must_be_blocked_gets_a_blocker(card_db, scripts):
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(MUST_BE_BLOCKED))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    result = enforce(board, combat, {})
    assert blocks(result, giant) == [bears.id]


def test_must_be_blocked_needs_only_one_blocker(card_db, scripts):
    """One requirement, satisfied once - not a requirement per creature."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(MUST_BE_BLOCKED))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    wurm = board.play("Craw Wurm", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    result = enforce(board, combat, {})
    assert len(result) == 1
    assert set(result) <= {giant.id, wurm.id}


def test_two_attackers_that_must_be_blocked_are_both_blocked(card_db, scripts):
    """The maximisation, at its simplest: two requirements, two blockers.

    A defending player who puts both blockers on one attacker obeys one
    requirement where two were possible, which CR 509.1c makes illegal.
    """
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(MUST_BE_BLOCKED))
    board.scripts.add("Craw Wurm", keyword(MUST_BE_BLOCKED))
    bears = board.play("Grizzly Bears", controller=0)
    wurm = board.play("Craw Wurm", controller=0)
    giant = board.play("Hill Giant", controller=1)
    spider = board.play("Giant Spider", controller=1)
    board.refresh()
    combat = attack_with(board, bears, wurm)

    result = enforce(board, combat, {giant.id: [bears.id], spider.id: [bears.id]})
    blocked = {attacker for attackers in result.values() for attacker in attackers}
    assert blocked == {bears.id, wurm.id}


# ---------------------------------------------------------------------------
# "All creatures able to block this creature do so"
# ---------------------------------------------------------------------------


def test_every_creature_able_to_block_is_forced_to(card_db, scripts):
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(BLOCKED_BY_ALL_ABLE))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    spider = board.play("Giant Spider", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    result = enforce(board, combat, {})
    assert blocks(result, giant) == [bears.id]
    assert blocks(result, spider) == [bears.id]


def test_a_creature_unable_to_block_it_is_not_forced(card_db, scripts):
    """"Able" is the whole point: flying still keeps the ground creature home."""
    board = combat_board(card_db, scripts)
    board.scripts.add(
        "Serra Angel", keyword("Flying"), keyword(BLOCKED_BY_ALL_ABLE)
    )
    angel = board.play("Serra Angel", controller=0)
    giant = board.play("Hill Giant", controller=1)
    spider = board.play("Giant Spider", controller=1)
    board.refresh()
    combat = attack_with(board, angel)

    result = enforce(board, combat, {})
    assert blocks(result, spider) == [angel.id]  # reach
    assert not blocks(result, giant)


def test_a_lured_creature_is_pulled_off_the_attacker_it_chose(card_db, scripts):
    """The requirement outranks the defending player's preference."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(BLOCKED_BY_ALL_ABLE))
    bears = board.play("Grizzly Bears", controller=0)
    wurm = board.play("Craw Wurm", controller=0)
    giant = board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, bears, wurm)

    result = enforce(board, combat, {giant.id: [wurm.id]})
    assert blocks(result, giant) == [bears.id]


# ---------------------------------------------------------------------------
# The example CR 509.1c prints: menace against a creature that must block
# ---------------------------------------------------------------------------


def test_menace_forces_the_second_creature_to_block_as_well(card_db, scripts):
    """CR 509.1c's own worked example.

    One creature "blocks if able", one has nothing. A menacing attacker can't
    be blocked by exactly one creature (CR 702.111b), so blocking with the
    forced creature alone is illegal and blocking with neither obeys no
    requirement. The only legal declaration that obeys the requirement is both.
    """
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword("Menace"))
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    wurm = board.play("Craw Wurm", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    result = enforce(board, combat, {})
    assert blocks(result, giant) == [bears.id]
    assert blocks(result, wurm) == [bears.id]


def test_menace_with_nobody_to_help_leaves_the_requirement_unobeyed(card_db, scripts):
    """No legal declaration obeys it, so the maximum obeyed is zero."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword("Menace"))
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    board.play("Hill Giant", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    assert enforce(board, combat, {}) == {}


# ---------------------------------------------------------------------------
# The obligations themselves
# ---------------------------------------------------------------------------


def test_obligations_are_one_per_requirement(card_db, scripts):
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword(BLOCKED_BY_ALL_ABLE))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    spider = board.play("Giant Spider", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    obligations = block_obligations(
        board.game,
        combat,
        PlayerId(1),
        [giant, spider],
    )
    assert len(obligations) == 2
    assert {o.blockers for o in obligations} == {(giant.id,), (spider.id,)}
    assert all(o.attackers == (bears.id,) for o in obligations)

    assert not obligations[0].satisfied({})
    assert obligations[0].satisfied({giant.id: bears.id})


# ---------------------------------------------------------------------------
# The corrected declaration survives CR 509.1's later steps
# ---------------------------------------------------------------------------


def test_the_forced_block_is_accepted_by_declare_blockers(card_db, scripts):
    """The enforced declaration is legal all the way through CR 509.1g.

    Driven through the real ``declare_blockers`` with an agent that returns
    what the enforcement produced, which is what the combat hook will do.
    """
    board = combat_board(card_db, scripts)
    board.scripts.add("Grizzly Bears", keyword("Menace"))
    bears = board.play("Grizzly Bears", controller=0)
    board.scripts.add("Hill Giant", keyword(BLOCKS_IF_ABLE))
    giant = board.play("Hill Giant", controller=1)
    wurm = board.play("Craw Wurm", controller=1)
    board.refresh()
    combat = attack_with(board, bears)

    board.game.agents[PlayerId(1)].blockers = enforce(board, combat, {})
    declare_blockers(board.game)

    assert combat.is_blocked(bears.id)
    assert sorted(combat.blockers[bears.id]) == sorted([giant.id, wurm.id])


# ---------------------------------------------------------------------------
# CR 508.8: an empty combat phase has no declare blockers or damage step
# ---------------------------------------------------------------------------


def steps_of(game, run) -> list[Step]:
    """Every step that actually began while ``run`` ran."""
    from mtgfish.rules.kernel.events import EventKind

    seen: list[Step] = []

    def observe(_game, event):
        if event.kind is EventKind.STEP_BEGAN:
            seen.append(Step(event.amount))

    previous = game.observer
    game.observer = observe
    try:
        run()
    finally:
        game.observer = previous
    return seen


def test_no_attackers_skips_declare_blockers_and_damage(card_db, scripts):
    from mtgfish.rules.cr500_turn_structure.cr500_turn import take_turn

    board = combat_board(card_db, scripts)
    board.play("Hill Giant", controller=1)
    game = board.game

    seen = steps_of(game, lambda: take_turn(game))
    assert Step.DECLARE_ATTACKERS in seen
    assert Step.END_OF_COMBAT in seen
    assert Step.DECLARE_BLOCKERS not in seen
    assert Step.COMBAT_DAMAGE not in seen


def test_an_attacker_brings_both_steps_back(card_db, scripts):
    from mtgfish.rules.cr500_turn_structure.cr500_turn import take_turn

    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.play("Hill Giant", controller=1)
    game = board.game
    game.agents[PlayerId(0)].attackers = {bears.id: 1}

    seen = steps_of(game, lambda: take_turn(game))
    assert Step.DECLARE_BLOCKERS in seen
    assert Step.COMBAT_DAMAGE in seen
