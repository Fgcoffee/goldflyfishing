"""State-based actions (CR 704).

The behaviours here are the ones players rely on without thinking about: a
creature with lethal damage dies, two creatures that kill each other both die,
a 0-toughness creature dies even through indestructible, and the legend rule
takes one of your two copies.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.enums import LossReason, Zone


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities(
        {
            "Darksteel Myr": (keyword("Indestructible"),),
            "Serra Angel": (keyword("Flying"), keyword("Vigilance")),
        }
    )


@pytest.fixture
def board(card_db, scripts):
    return make_board(card_db, scripts)


# ---------------------------------------------------------------------------
# Players (CR 704.5a-c)
# ---------------------------------------------------------------------------


def test_zero_life_loses(board):
    board.game.player(0).life = 0
    board.sba()
    assert board.game.player(0).has_lost
    assert board.game.player(0).loss_reason is LossReason.LIFE


def test_negative_life_loses(board):
    board.game.player(0).life = -3
    board.sba()
    assert board.game.player(0).has_lost


def test_ten_poison_loses(board):
    board.game.player(0).poison = 10
    board.sba()
    assert board.game.player(0).loss_reason is LossReason.POISON


def test_nine_poison_survives(board):
    board.game.player(0).poison = 9
    board.sba()
    assert not board.game.player(0).has_lost


def test_drawing_from_an_empty_library_loses_on_the_next_check(board):
    """CR 704.5b: the draw sets a flag; the loss is a state-based action."""
    player = board.game.player(0)
    player.library.clear()
    board.game.draw(player.id, 1)
    assert not player.has_lost

    board.sba()
    assert player.loss_reason is LossReason.EMPTY_LIBRARY


# ---------------------------------------------------------------------------
# Creatures (CR 704.5f-h)
# ---------------------------------------------------------------------------


def test_lethal_damage_destroys(board):
    bears = board.play("Grizzly Bears")  # 2/2
    bears.damage = 2
    board.sba()
    assert bears.id not in board.game.battlefield


def test_damage_below_lethal_survives(board):
    bears = board.play("Grizzly Bears")
    bears.damage = 1
    board.sba()
    assert bears.id in board.game.battlefield


def test_indestructible_survives_lethal_damage(board):
    """CR 702.12b."""
    myr = board.play("Darksteel Myr")
    myr.damage = 99
    board.sba()
    assert myr.id in board.game.battlefield


def test_zero_toughness_dies_through_indestructible(board):
    """CR 704.5f is not destruction, so indestructible does not help.

    This is the distinction that makes -X/-X removal the answer to an
    indestructible creature.
    """
    myr = board.play("Darksteel Myr")
    myr.add_counters("-1/-1", 10)
    board.refresh()
    board.sba()
    assert myr.id not in board.game.battlefield


def test_deathtouch_damage_destroys_regardless_of_amount(board):
    """CR 704.5h: any nonzero damage from a deathtouch source is lethal."""
    angel = board.play("Serra Angel")  # 4/4
    angel.damage = 1
    angel.dealt_deathtouch_damage = True
    board.sba()
    assert angel.id not in board.game.battlefield


def test_deathtouch_flag_without_damage_does_nothing(board):
    angel = board.play("Serra Angel")
    angel.dealt_deathtouch_damage = True
    board.sba()
    assert angel.id in board.game.battlefield


def test_two_creatures_that_kill_each_other_both_die(board):
    """CR 704.3: state-based actions are simultaneous.

    Processing them one at a time would let the first death stop the second.
    """
    first = board.play("Grizzly Bears", controller=0)
    second = board.play("Grizzly Bears", controller=1)
    first.damage = 2
    second.damage = 2
    board.sba()
    assert first.id not in board.game.battlefield
    assert second.id not in board.game.battlefield


# ---------------------------------------------------------------------------
# Counters (CR 704.5q)
# ---------------------------------------------------------------------------


def test_plus_and_minus_counters_annihilate(board):
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 3)
    bears.add_counters("-1/-1", 2)
    board.refresh()
    board.sba()
    assert bears.counters.get("+1/+1", 0) == 1
    assert bears.counters.get("-1/-1", 0) == 0
    assert board.pt(bears) == (3, 3)


def test_equal_counters_annihilate_completely(board):
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 2)
    bears.add_counters("-1/-1", 2)
    board.refresh()
    board.sba()
    assert bears.counters == {}
    assert board.pt(bears) == (2, 2)


# ---------------------------------------------------------------------------
# Tokens and copies (CR 704.5d-e)
# ---------------------------------------------------------------------------


def test_token_in_the_graveyard_ceases_to_exist(board):
    token = board.token("Grizzly Bears")
    board.game.move_object(token, Zone.GRAVEYARD)
    board.sba()
    assert token.id not in board.game.objects
    assert not board.game.player(0).graveyard


# ---------------------------------------------------------------------------
# Legend and world rules (CR 704.5j-k)
# ---------------------------------------------------------------------------


def test_legend_rule_keeps_one(board):
    first = board.play("Kenrith, the Returned King", controller=0)
    second = board.play("Kenrith, the Returned King", controller=0)
    board.sba()
    survivors = [o for o in (first, second) if o.id in board.game.battlefield]
    assert len(survivors) == 1


def test_legend_rule_is_per_player(board):
    """Two opponents may each control their own copy of the same legend."""
    mine = board.play("Kenrith, the Returned King", controller=0)
    theirs = board.play("Kenrith, the Returned King", controller=1)
    board.sba()
    assert mine.id in board.game.battlefield
    assert theirs.id in board.game.battlefield


def test_non_legendary_duplicates_are_fine(board):
    a = board.play("Grizzly Bears")
    b = board.play("Grizzly Bears")
    board.sba()
    assert a.id in board.game.battlefield
    assert b.id in board.game.battlefield


# ---------------------------------------------------------------------------
# Auras and Equipment (CR 704.5m-n)
# ---------------------------------------------------------------------------


def test_aura_attached_to_nothing_goes_to_the_graveyard(board):
    aura = board.play("Pacifism")
    board.sba()
    assert aura.id not in board.game.battlefield


def test_equipment_attached_to_nothing_simply_stays(board):
    """CR 704.5n: Equipment falls off, it does not die."""
    equipment = board.play("Bonesplitter")
    board.sba()
    assert equipment.id in board.game.battlefield


# ---------------------------------------------------------------------------
# Planeswalkers (CR 704.5i)
# ---------------------------------------------------------------------------


def test_planeswalker_with_no_loyalty_dies(board):
    walker = board.play("Ajani, Caller of the Pride")
    walker.counters.pop("loyalty", None)
    board.sba()
    assert walker.id not in board.game.battlefield


def test_planeswalker_with_loyalty_survives(board):
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.sba()
    assert walker.id in board.game.battlefield


# ---------------------------------------------------------------------------
# Repetition (CR 704.3)
# ---------------------------------------------------------------------------


def test_actions_repeat_until_nothing_applies(board):
    """One pass can create work for the next, so they run to a fixed point."""
    bears = board.play("Grizzly Bears")
    bears.damage = 2
    board.game.player(1).life = 0
    board.sba()
    assert bears.id not in board.game.battlefield
    assert board.game.player(1).has_lost
