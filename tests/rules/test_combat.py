"""Combat (CR 506-511).

Attack and block legality, evasion, damage assignment order, first strike,
trample, deathtouch, and the rules that make combat maths work out the way
players expect.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.combat import (
    can_attack,
    can_block,
    deal_combat_damage,
    declare_attackers,
    declare_blockers,
    end_combat,
)
from mtgfish.rules.ids import PlayerId

from harness import FixedAgent, ScriptedAbilities, keyword, make_board


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities(
        {
            "Serra Angel": (keyword("Flying"), keyword("Vigilance")),
            "Giant Spider": (keyword("Reach"),),
            "Typhoid Rats": (keyword("Deathtouch"),),
            "Rhox": (keyword("Trample"),),
            "White Knight": (keyword("First strike"),),
            "Boros Swiftblade": (keyword("Double strike"),),
            "Wall of Omens": (keyword("Defender"),),
            "Vampire Nighthawk": (
                keyword("Flying"),
                keyword("Deathtouch"),
                keyword("Lifelink"),
            ),
            "Goblin War Drums": (keyword("Menace"),),
        }
    )


def combat_board(card_db, scripts, *, attackers=None, blockers=None, order=None):
    board = make_board(card_db, scripts)
    board.game.agents[PlayerId(0)] = FixedAgent(attackers=attackers or {})
    board.game.agents[PlayerId(1)] = FixedAgent(
        blockers=blockers or {}, blocker_order=order or {}
    )
    board.game.active_player = PlayerId(0)
    return board


# ---------------------------------------------------------------------------
# Attack legality (CR 508.1a)
# ---------------------------------------------------------------------------


def test_untapped_creature_can_attack(card_db, scripts):
    board = make_board(card_db, scripts)
    assert can_attack(board.game, board.play("Grizzly Bears"))


def test_tapped_creature_cannot_attack(card_db, scripts):
    board = make_board(card_db, scripts)
    assert not can_attack(board.game, board.play("Grizzly Bears", tapped=True))


def test_summoning_sick_creature_cannot_attack(card_db, scripts):
    """CR 302.6."""
    board = make_board(card_db, scripts)
    bears = board.play("Grizzly Bears")
    bears.summoning_sick = True
    assert not can_attack(board.game, bears)


def test_haste_beats_summoning_sickness(card_db, scripts):
    board = make_board(card_db, scripts)
    board.scripts.add("Raging Goblin", keyword("Haste"))
    goblin = board.play("Raging Goblin")
    goblin.summoning_sick = True
    board.refresh()
    assert can_attack(board.game, goblin)


def test_defender_cannot_attack(card_db, scripts):
    """CR 702.3b."""
    board = make_board(card_db, scripts)
    assert not can_attack(board.game, board.play("Wall of Omens"))


def test_non_creature_cannot_attack(card_db, scripts):
    board = make_board(card_db, scripts)
    assert not can_attack(board.game, board.play("Sol Ring"))


# ---------------------------------------------------------------------------
# Declaring attackers (CR 508.1f)
# ---------------------------------------------------------------------------


def test_attacking_taps_the_creature(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.game.agents[PlayerId(0)].attackers = {bears.id: 1}

    declare_attackers(board.game)
    assert bears.tapped
    assert board.game.combat.is_attacking(bears.id)


def test_vigilance_attacks_without_tapping(card_db, scripts):
    """CR 702.21b."""
    board = combat_board(card_db, scripts)
    angel = board.play("Serra Angel", controller=0)
    board.game.agents[PlayerId(0)].attackers = {angel.id: 1}

    declare_attackers(board.game)
    assert not angel.tapped
    assert board.game.combat.is_attacking(angel.id)


def test_illegal_attacker_is_rejected(card_db, scripts):
    """A declaration that breaks a restriction is not honoured (CR 508.1d)."""
    board = combat_board(card_db, scripts)
    wall = board.play("Wall of Omens", controller=0)
    board.game.agents[PlayerId(0)].attackers = {wall.id: 1}

    declare_attackers(board.game)
    assert not board.game.combat.is_attacking(wall.id)


def test_goaded_creature_is_forced_to_attack(card_db, scripts):
    """CR 701.38: goad is a requirement, and requirements are maximised."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    bears.goaded_by.add(PlayerId(1))
    board.game.agents[PlayerId(0)].attackers = {}  # The bot would rather not.

    declare_attackers(board.game)
    assert board.game.combat.is_attacking(bears.id)


def test_goaded_creature_attacks_someone_else(card_db, scripts):
    """CR 701.38b: it must attack a player who did not goad it, if it can."""
    board = make_board(card_db, scripts, players=3)
    board.game.agents[PlayerId(0)] = FixedAgent()
    board.game.active_player = PlayerId(0)
    bears = board.play("Grizzly Bears", controller=0)
    bears.goaded_by.add(PlayerId(1))

    declare_attackers(board.game)
    assert board.game.combat.attacking[bears.id] != 1


# ---------------------------------------------------------------------------
# Block legality and evasion (CR 509.1b)
# ---------------------------------------------------------------------------


def test_ground_creature_cannot_block_flier(card_db, scripts):
    """CR 702.9b."""
    board = make_board(card_db, scripts)
    flier = board.play("Serra Angel", controller=0)
    ground = board.play("Grizzly Bears", controller=1)
    assert not can_block(board.game, ground, flier)


def test_flier_can_block_flier(card_db, scripts):
    board = make_board(card_db, scripts)
    attacker = board.play("Serra Angel", controller=0)
    blocker = board.play("Vampire Nighthawk", controller=1)
    assert can_block(board.game, blocker, attacker)


def test_reach_can_block_flier(card_db, scripts):
    board = make_board(card_db, scripts)
    flier = board.play("Serra Angel", controller=0)
    spider = board.play("Giant Spider", controller=1)
    assert can_block(board.game, spider, flier)


def test_flier_can_block_ground_creature(card_db, scripts):
    """Flying restricts who can block it, not what it can block."""
    board = make_board(card_db, scripts)
    ground = board.play("Grizzly Bears", controller=0)
    flier = board.play("Serra Angel", controller=1)
    assert can_block(board.game, flier, ground)


def test_tapped_creature_cannot_block(card_db, scripts):
    from mtgfish.rules.combat import can_block_at_all

    board = make_board(card_db, scripts)
    assert not can_block_at_all(board.game, board.play("Grizzly Bears", tapped=True))


def test_summoning_sick_creature_can_still_block(card_db, scripts):
    """CR 302.6 restricts attacking and {T} abilities, never blocking."""
    from mtgfish.rules.combat import can_block_at_all

    board = make_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=1)
    bears.summoning_sick = True
    assert can_block_at_all(board.game, bears)


def test_menace_needs_two_blockers(card_db, scripts):
    """CR 702.111b: a single declared blocker is an illegal declaration."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Goblin Piker", keyword("Menace"))
    attacker = board.play("Goblin Piker", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    board.refresh()

    board.game.agents[PlayerId(0)].attackers = {attacker.id: 1}
    board.game.agents[PlayerId(1)].blockers = {blocker.id: [attacker.id]}

    declare_attackers(board.game)
    declare_blockers(board.game)
    assert not board.game.combat.blockers.get(attacker.id)


def test_menace_allows_two_blockers(card_db, scripts):
    board = combat_board(card_db, scripts)
    board.scripts.add("Goblin Piker", keyword("Menace"))
    attacker = board.play("Goblin Piker", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    board.refresh()

    board.game.agents[PlayerId(0)].attackers = {attacker.id: 1}
    board.game.agents[PlayerId(1)].blockers = {
        first.id: [attacker.id],
        second.id: [attacker.id],
    }

    declare_attackers(board.game)
    declare_blockers(board.game)
    assert len(board.game.combat.blockers.get(attacker.id, [])) == 2


# ---------------------------------------------------------------------------
# Combat damage (CR 510)
# ---------------------------------------------------------------------------


def _fight(board, attacker, blockers=(), defender=1):
    board.game.agents[PlayerId(0)].attackers = {attacker.id: defender}
    board.game.agents[PlayerId(1)].blockers = {b.id: [attacker.id] for b in blockers}
    declare_attackers(board.game)
    declare_blockers(board.game)
    deal_combat_damage(board.game)
    board.sba()


def test_unblocked_attacker_damages_the_player(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    _fight(board, bears)
    assert board.game.player(1).life == 38


def test_blocked_attacker_does_not_damage_the_player(card_db, scripts):
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    _fight(board, attacker, [blocker])
    assert board.game.player(1).life == 40


def test_trading_creatures_both_die(card_db, scripts):
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    _fight(board, attacker, [blocker])
    assert attacker.id not in board.game.battlefield
    assert blocker.id not in board.game.battlefield


def test_trample_carries_excess_to_the_player(card_db, scripts):
    """CR 702.19b: lethal to the blockers, the rest to the defending player."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Rhox", keyword("Trample"))
    rhox = board.play("Rhox", controller=0)  # 5/4 trample
    blocker = board.play("Grizzly Bears", controller=1)  # 2/2
    board.refresh()
    _fight(board, rhox, [blocker])
    # 2 damage kills the Bears, 3 tramples over.
    assert board.game.player(1).life == 37


def test_no_trample_means_no_excess(card_db, scripts):
    """CR 510.1c: without trample, all the damage stays on the blockers."""
    board = combat_board(card_db, scripts)
    giant = board.play("Hill Giant", controller=0)  # 3/3 vanilla
    blocker = board.play("Grizzly Bears", controller=1)  # 2/2
    _fight(board, giant, [blocker])
    assert board.game.player(1).life == 40
    assert blocker.id not in board.game.battlefield


def test_deathtouch_makes_one_damage_lethal(card_db, scripts):
    """CR 702.2b."""
    board = combat_board(card_db, scripts)
    rats = board.play("Typhoid Rats", controller=0)  # 1/1 deathtouch
    angel = board.play("Serra Angel", controller=1)  # 4/4
    _fight(board, rats, [angel])
    assert angel.id not in board.game.battlefield


def test_deathtouch_and_trample_assign_only_one_per_blocker(card_db, scripts):
    """CR 702.2c: with deathtouch, 1 damage is lethal for assignment.

    A 5/5 deathtouch trampler blocked by a 4/4 assigns 1 and tramples 4.
    """
    board = combat_board(card_db, scripts)
    board.scripts.add("Rhox", keyword("Trample"), keyword("Deathtouch"))
    rhox = board.play("Rhox", controller=0)  # 5/4
    blocker = board.play("Serra Angel", controller=1)  # 4/4
    board.refresh()
    _fight(board, rhox, [blocker])
    assert board.game.player(1).life == 36  # 5 power - 1 assigned = 4 trampled
    assert blocker.id not in board.game.battlefield


def test_lifelink_gains_life(card_db, scripts):
    board = combat_board(card_db, scripts)
    hawk = board.play("Vampire Nighthawk", controller=0)  # 2/3 lifelink
    _fight(board, hawk)
    assert board.game.player(0).life == 42
    assert board.game.player(1).life == 38


def test_first_strike_kills_before_the_normal_damage_step(card_db, scripts):
    """CR 510.4: the first-strike step happens first, and the loser is gone."""
    board = combat_board(card_db, scripts)
    board.scripts.add("White Knight", keyword("First strike"))
    knight = board.play("White Knight", controller=0)  # 2/2 first strike
    bears = board.play("Grizzly Bears", controller=1)  # 2/2
    board.refresh()
    _fight(board, knight, [bears])
    assert bears.id not in board.game.battlefield
    assert knight.id in board.game.battlefield


def test_double_strike_deals_damage_twice(card_db, scripts):
    """CR 702.4b."""
    board = combat_board(card_db, scripts)
    board.scripts.add("Boros Swiftblade", keyword("Double strike"))
    blade = board.play("Boros Swiftblade", controller=0)  # 1/2 double strike
    board.refresh()
    _fight(board, blade)
    assert board.game.player(1).life == 38


def test_damage_assignment_order_decides_who_dies(card_db, scripts):
    """CR 509.2, 510.1a: lethal to the first blocker before any to the second."""
    board = combat_board(card_db, scripts)
    attacker = board.play("Serra Angel", controller=0)  # 4/4 flying
    first = board.play("Vampire Nighthawk", controller=1)  # 2/3 flier
    second = board.play("Giant Spider", controller=1)  # 2/4 reach
    board.refresh()

    board.game.agents[PlayerId(0)].attackers = {attacker.id: 1}
    board.game.agents[PlayerId(0)].blocker_order = {attacker.id: [first.id, second.id]}
    board.game.agents[PlayerId(1)].blockers = {
        first.id: [attacker.id],
        second.id: [attacker.id],
    }
    declare_attackers(board.game)
    declare_blockers(board.game)
    deal_combat_damage(board.game)
    board.sba()

    # 3 to the Nighthawk kills it; only 1 is left for the 4-toughness Spider.
    assert first.id not in board.game.battlefield
    assert second.id in board.game.battlefield


def test_a_blocked_creature_whose_blocker_leaves_still_deals_nothing(card_db, scripts):
    """CR 509.1h: once blocked, always blocked."""
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)

    board.game.agents[PlayerId(0)].attackers = {attacker.id: 1}
    board.game.agents[PlayerId(1)].blockers = {blocker.id: [attacker.id]}
    declare_attackers(board.game)
    declare_blockers(board.game)

    # The blocker is removed before damage.
    board.game.move_object(blocker, blocker.zone.GRAVEYARD)
    deal_combat_damage(board.game)

    assert board.game.player(1).life == 40


def test_end_of_combat_clears_everything(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.game.agents[PlayerId(0)].attackers = {bears.id: 1}
    declare_attackers(board.game)
    assert board.game.combat.is_attacking(bears.id)

    end_combat(board.game)
    assert not board.game.combat.is_attacking(bears.id)


def test_an_attacker_is_neither_blocked_nor_unblocked_until_blockers_are_declared(
    card_db, scripts
):
    """CR 509.1h: blocked and unblocked are decided by the declaration, so
    before it neither description matches anything."""
    from mtgfish.rules.matching import find
    from mtgfish.rules.query import ObjectFilter

    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.game.agents[PlayerId(0)].attackers = {bears.id: 1}
    unblocked = ObjectFilter(attacking=True, blocked=False)
    blocked = ObjectFilter(attacking=True, blocked=True)

    declare_attackers(board.game)
    assert not find(board.game, unblocked)
    assert not find(board.game, blocked)

    declare_blockers(board.game)
    assert [obj.id for obj in find(board.game, unblocked)] == [bears.id]
    assert not find(board.game, blocked)

    end_combat(board.game)
    assert not find(board.game, unblocked)


def test_attackers_are_still_attacking_during_the_end_of_combat_step(card_db, scripts):
    """CR 511.3: creatures leave combat as the end of combat step *ends*, so a
    player with priority during that step still sees them attacking."""
    from mtgfish.rules.enums import Phase, Step
    from mtgfish.rules.turn import TurnOptions, _run_step

    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    board.game.agents[PlayerId(0)].attackers = {bears.id: 1}
    declare_attackers(board.game)

    seen: list[bool] = []

    class Watcher(FixedAgent):
        def choose_action(self, game, player, legal):
            seen.append(game.combat.is_attacking(bears.id))
            return super().choose_action(game, player, legal)

    board.game.agents[PlayerId(0)] = Watcher()
    board.game.agents[PlayerId(1)] = Watcher()
    _run_step(board.game, Phase.COMBAT, Step.END_OF_COMBAT, TurnOptions())

    assert seen and all(seen)
    assert not board.game.combat.is_attacking(bears.id)
