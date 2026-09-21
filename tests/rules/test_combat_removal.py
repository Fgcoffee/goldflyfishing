"""Removal from combat (CR 506.4).

A permanent stops being an attacker, a blocker, or a permanent that's being
attacked the moment one of a short list of things happens to it - it leaves
the battlefield, its controller changes, it phases out, it stops being the
card type that put it there. Until this was implemented the engine only ever
took a regenerated creature out of combat, so a creature that died, was
stolen, phased out or was turned into an artifact kept its place in the
combat record and kept dealing damage from it.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    check_removal_from_combat,
    deal_combat_damage,
    declare_attackers,
    declare_blockers,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Duration, Layer, Phase, Step
from mtgfish.rules.kernel.game import ContinuousEffect
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.rules.kernel.values import Value


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities({"Rhox": (keyword("Trample"),)})


def combat_board(card_db, scripts):
    board = make_board(card_db, scripts)
    board.game.agents[PlayerId(0)] = FixedAgent()
    board.game.agents[PlayerId(1)] = FixedAgent()
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.COMBAT
    board.game.step = Step.DECLARE_ATTACKERS
    return board


def attack(board, attacks, blocks=()):
    """``attacks`` is (attacker, player or AttackPermanent) pairs."""
    board.game.agents[PlayerId(0)].attackers = {obj.id: at for obj, at in attacks}
    board.game.agents[PlayerId(1)].blockers = {
        blocker.id: [attacker.id] for blocker, attacker in blocks
    }
    declare_attackers(board.game)


def block(board, blocks=()):
    board.game.step = Step.DECLARE_BLOCKERS
    board.game.agents[PlayerId(1)].blockers = {
        blocker.id: [attacker.id] for blocker, attacker in blocks
    }
    declare_blockers(board.game)


def damage(board):
    board.game.step = Step.COMBAT_DAMAGE
    deal_combat_damage(board.game)
    board.sba()


def _continuous(board, effect, layer, controller=0):
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=effect,
            source=NO_OBJECT,
            controller=PlayerId(controller),
            timestamp=board.game.ids.timestamp(),
            layer=int(layer),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.game.invalidate_characteristics()


def steal(board, obj, thief):
    """Layer 2: another player gains control of ``obj`` (CR 613.1b)."""
    _continuous(
        board,
        Effect(EffectKind.GAIN_CONTROL, targets=ObjectFilter(specific=(obj.id,))),
        Layer.CONTROL,
        controller=thief,
    )
    board.chars(obj)  # Layer 2 writes the new controller as the board is built.


def lose_type(board, obj, types):
    """Layer 4: ``obj`` stops being the given card type (CR 613.1d)."""
    _continuous(
        board,
        Effect(
            EffectKind.REMOVE_TYPE, targets=ObjectFilter(specific=(obj.id,)), types=types
        ),
        Layer.TYPE,
    )
    board.chars(obj)


def gain_type(board, obj, types):
    """Layer 4: ``obj`` becomes the given card type in addition."""
    _continuous(
        board,
        Effect(
            EffectKind.ADD_TYPE, targets=ObjectFilter(specific=(obj.id,)), types=types
        ),
        Layer.TYPE,
    )
    board.chars(obj)


# ---------------------------------------------------------------------------
# Leaving the battlefield
# ---------------------------------------------------------------------------


def test_attacker_that_leaves_the_battlefield_is_removed_from_combat(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    assert board.game.combat.is_attacking(bears.id)

    actions.exile(board.game, bears)
    board.sba()
    assert not board.game.combat.is_attacking(bears.id)
    assert bears.id not in board.game.combat.attacking


def test_blocker_that_leaves_the_battlefield_is_removed_from_combat(card_db, scripts):
    """But the attacker stays blocked (CR 509.1h)."""
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    attack(board, [(attacker, 1)])
    block(board, [(blocker, attacker)])
    assert board.game.combat.is_blocking(blocker.id)

    actions.exile(board.game, blocker)
    board.sba()
    assert not board.game.combat.is_blocking(blocker.id)
    assert blocker.id not in board.game.combat.blocking
    assert board.game.combat.is_blocked(attacker.id)

    damage(board)
    assert board.game.player(1).life == 40


# ---------------------------------------------------------------------------
# Change of controller
# ---------------------------------------------------------------------------


def test_attacker_whose_controller_changes_is_removed_from_combat(card_db, scripts):
    """Otherwise the creature keeps attacking for whoever stole it."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    assert board.game.combat.is_attacking(bears.id)

    steal(board, bears, thief=1)
    check_removal_from_combat(board.game)
    assert not board.game.combat.is_attacking(bears.id)


def test_stolen_attacker_deals_no_combat_damage(card_db, scripts):
    """The bug this closes: damage read the *current* controller, so a stolen
    attacker dealt its damage on behalf of the player who took it, to the
    player it had been sent at."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    block(board)
    steal(board, bears, thief=1)
    damage(board)
    assert board.game.player(1).life == 40
    assert board.game.player(0).life == 40


def test_blocker_whose_controller_changes_is_removed_from_combat(card_db, scripts):
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    attack(board, [(attacker, 1)])
    block(board, [(blocker, attacker)])

    steal(board, blocker, thief=0)
    check_removal_from_combat(board.game)
    assert not board.game.combat.is_blocking(blocker.id)
    # It is no longer in combat, so it neither deals nor takes combat damage.
    damage(board)
    assert blocker.damage == 0
    assert board.game.player(1).life == 40


# ---------------------------------------------------------------------------
# Phasing out
# ---------------------------------------------------------------------------


def test_phased_out_attacker_is_removed_from_combat(card_db, scripts):
    """CR 702.26b: a phased-out permanent is treated as though it did not
    exist, so it cannot still be attacking."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    bears.phased_out = True
    board.refresh()

    check_removal_from_combat(board.game)
    assert not board.game.combat.is_attacking(bears.id)


def test_phased_out_attacker_deals_no_combat_damage(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    block(board)
    bears.phased_out = True
    board.refresh()
    damage(board)
    assert board.game.player(1).life == 40


# ---------------------------------------------------------------------------
# Stops being a creature
# ---------------------------------------------------------------------------


def test_attacker_that_stops_being_a_creature_is_removed_from_combat(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    lose_type(board, bears, CardType.CREATURE)

    check_removal_from_combat(board.game)
    assert not board.game.combat.is_attacking(bears.id)


def test_blocker_that_stops_being_a_creature_is_removed_from_combat(card_db, scripts):
    """The attacker is still blocked, so it still deals no damage to the
    player (CR 509.1h) - but the ex-creature is out of combat entirely and so
    takes none of it."""
    board = combat_board(card_db, scripts)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    attack(board, [(attacker, 1)])
    block(board, [(blocker, attacker)])
    lose_type(board, blocker, CardType.CREATURE)

    check_removal_from_combat(board.game)
    assert not board.game.combat.is_blocking(blocker.id)
    damage(board)
    assert blocker.damage == 0
    assert board.game.player(1).life == 40


def test_attacker_that_becomes_a_battle_is_removed_from_combat(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    gain_type(board, bears, CardType.BATTLE)

    check_removal_from_combat(board.game)
    assert not board.game.combat.is_attacking(bears.id)


# ---------------------------------------------------------------------------
# The permanent that's being attacked
# ---------------------------------------------------------------------------


def test_attacked_planeswalker_that_stops_being_one_stops_being_attacked(
    card_db, scripts
):
    """CR 506.4c: the attacker stays in combat, attacking nothing, and so
    deals no damage - not to the planeswalker, and not to its controller."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    jace = board.play("Jace Beleren", controller=1)
    jace.add_counters("loyalty", 3)
    board.refresh()
    attack(board, [(bears, AttackPermanent(jace.id))])
    assert board.game.combat.attacking_permanent.get(bears.id) == jace.id

    lose_type(board, jace, CardType.PLANESWALKER)
    check_removal_from_combat(board.game)
    assert board.game.combat.attacking_permanent.get(bears.id) != jace.id
    assert board.game.combat.is_attacking(bears.id)

    block(board)
    damage(board)
    assert jace.counter_count("loyalty") == 3
    assert board.game.player(1).life == 40


def test_attacked_planeswalker_that_leaves_stops_being_attacked(card_db, scripts):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    jace = board.play("Jace Beleren", controller=1)
    jace.add_counters("loyalty", 3)
    board.refresh()
    attack(board, [(bears, AttackPermanent(jace.id))])

    actions.exile(board.game, jace)
    board.sba()
    assert board.game.combat.attacking_permanent.get(bears.id) != jace.id
    assert board.game.combat.is_attacking(bears.id)


def test_attacked_planeswalker_whose_controller_changes_stops_being_attacked(
    card_db, scripts
):
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    jace = board.play("Jace Beleren", controller=1)
    jace.add_counters("loyalty", 3)
    board.refresh()
    attack(board, [(bears, AttackPermanent(jace.id))])

    steal(board, jace, thief=0)
    check_removal_from_combat(board.game)
    assert board.game.combat.attacking_permanent.get(bears.id) != jace.id
    assert board.game.combat.is_attacking(bears.id)


# ---------------------------------------------------------------------------
# What does *not* remove a permanent from combat
# ---------------------------------------------------------------------------


def test_gaining_defender_does_not_remove_a_declared_attacker(card_db, scripts):
    """CR 506.4a: something that would have stopped it attacking is too late
    once it has been declared."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    _continuous(
        board,
        Effect(
            EffectKind.GRANT_ABILITY,
            targets=ObjectFilter(specific=(bears.id,)),
            keywords=("Defender",),
        ),
        Layer.ABILITY,
    )
    check_removal_from_combat(board.game)
    assert board.game.combat.is_attacking(bears.id)

    block(board)
    damage(board)
    assert board.game.player(1).life == 38


def test_tapping_an_attacker_does_not_remove_it_from_combat(card_db, scripts):
    """CR 506.4b."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    actions.untap(board.game, bears)
    actions.tap(board.game, bears)
    check_removal_from_combat(board.game)
    assert board.game.combat.is_attacking(bears.id)

    block(board)
    damage(board)
    assert board.game.player(1).life == 38


def test_a_creature_that_only_loses_power_stays_in_combat(card_db, scripts):
    """Nothing about being a 0/1 removes a creature from combat."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    _continuous(
        board,
        Effect(
            EffectKind.SET_PT,
            targets=ObjectFilter(specific=(bears.id,)),
            amount=Value.of(0),
            amount2=Value.of(1),
        ),
        Layer.PT_SET,
    )
    check_removal_from_combat(board.game)
    assert board.game.combat.is_attacking(bears.id)


def test_regenerating_an_attacker_removes_it_from_combat(card_db, scripts):
    """CR 701.15: the one case the engine already handled, kept honest."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    bears.regeneration_shields = 1
    assert not actions.destroy(board.game, bears)
    assert not board.game.combat.is_attacking(bears.id)


def test_removal_from_combat_is_swept_before_combat_damage(card_db, scripts):
    """Nothing has to remember to call the check: the damage step does."""
    board = combat_board(card_db, scripts)
    bears = board.play("Grizzly Bears", controller=0)
    attack(board, [(bears, 1)])
    block(board)
    lose_type(board, bears, CardType.CREATURE)
    damage(board)
    assert not board.game.combat.is_attacking(bears.id)
    assert board.game.player(1).life == 40
