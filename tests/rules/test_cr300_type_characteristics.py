"""Characteristics only one card type has (CR 208.3, 301.7a, 301.7b, 306.5, 310.4).

A Vehicle prints a power and a toughness and is not a creature. A planeswalker's
loyalty is its counters, not the number in the corner. A battle's defense is the
same story. The engine read all three straight off the card, so an uncrewed
Smuggler's Copter answered "3/3" to anything that asked, and a planeswalker that
had been attacked down to one loyalty still answered "4".

These check the rules themselves, in
``mtgfish/rules/cr300_card_types/cr300_characteristics.py``, and the call
sites that reach them: the layer system for power and toughness, the
enters-the-battlefield path for loyalty and defense counters, and the filter
machinery for reading a planeswalker's loyalty off the board.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr300_card_types.cr300_characteristics import (
    defense,
    entering_counters,
    loyalty,
    settle_type_characteristics,
)
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build

#: A Vehicle with a plain printed power and toughness, and no crew cost that
#: these tests would have to pay. "Living metal" (CR 702.161a) animates it on
#: its controller's turn, which is a cheaper way to make it a creature than
#: tapping crew and is the same layer-4 type change.
VEHICLE = "Smuggler's Copter"  # Artifact - Vehicle, 3/3
PLANESWALKER = "Chandra, Torch of Defiance"  # printed loyalty 4
BATTLE = "Invasion of Gobakhan"  # printed defense 3


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def settled(board, obj):
    """The object's characteristics with the card-type rules applied."""
    return settle_type_characteristics(obj, board.chars(obj))


def animate(board, name):
    """Give a card "living metal", so it is a creature on its controller's turn."""
    board.scripts.add(name, *build(KeywordInstance("Living metal")))
    board.refresh()


# ---------------------------------------------------------------------------
# CR 208.3 / 301.7a / 301.7b: power and toughness
# ---------------------------------------------------------------------------


def test_a_vehicle_on_the_battlefield_has_no_power_or_toughness(board):
    """CR 301.7a: the printed numbers are the Vehicle's only while it is also a
    creature. Uncrewed, it is a plain artifact and has neither."""
    vehicle = board.play(VEHICLE, controller=0)

    assert board.game.printed_characteristics(vehicle).power == 3  # the card says 3/3
    chars = settled(board, vehicle)
    assert chars.power is None
    assert chars.toughness is None


def test_a_vehicle_that_is_a_creature_has_its_printed_power_and_toughness(board):
    """CR 301.7b: becoming a creature hands them straight back."""
    animate(board, VEHICLE)
    vehicle = board.play(VEHICLE, controller=0)
    board.game.active_player = 0
    board.refresh()

    assert board.chars(vehicle).is_creature
    assert (settled(board, vehicle).power, settled(board, vehicle).toughness) == (3, 3)


def test_the_same_vehicle_loses_them_again_when_it_stops_being_a_creature(board):
    """Living metal animates it only on its controller's turn (CR 702.161a), so
    the same permanent answers both ways within one test."""
    animate(board, VEHICLE)
    vehicle = board.play(VEHICLE, controller=0)

    board.game.active_player = 0
    board.refresh()
    assert settled(board, vehicle).power == 3

    board.game.active_player = 1
    board.refresh()
    assert settled(board, vehicle).power is None


def test_a_modification_made_while_it_was_not_a_creature_still_applies(board):
    """CR 208.3a: an effect that modifies a noncreature permanent's power is
    created anyway, and does its work once the permanent becomes a creature.

    A +1/+1 counter is the same shape of thing in layer 7d, and needs no
    until-end-of-turn bookkeeping to set up.
    """
    animate(board, VEHICLE)
    vehicle = board.play(VEHICLE, controller=0)
    board.game.active_player = 1  # not its controller's turn: not a creature
    board.refresh()
    vehicle.add_counters("+1/+1", 1)
    board.refresh()

    assert settled(board, vehicle).power is None

    board.game.active_player = 0
    board.refresh()
    assert (settled(board, vehicle).power, settled(board, vehicle).toughness) == (4, 4)


def test_a_creature_keeps_what_the_layers_gave_it(board):
    """The control: nothing here touches an ordinary creature."""
    bear = board.play("Grizzly Bears", controller=0)

    assert (settled(board, bear).power, settled(board, bear).toughness) == (2, 2)


def test_a_permanent_with_no_printed_power_is_left_alone(board):
    """An enchantment has no power to hide, and comes back unchanged."""
    aura = board.play("Pacifism", controller=0)
    chars = board.chars(aura)

    assert settle_type_characteristics(aura, chars) is chars


def test_a_vehicle_off_the_battlefield_keeps_its_printed_power(board):
    """CR 208.3, second sentence: only *permanents* lose them. A Vehicle card in
    a hand or a graveyard is still printed 3/3, which is what "creature card
    with power 3 or less" style wording has to read."""
    in_hand = board.hand(VEHICLE, controller=0)
    in_graveyard = board.graveyard(VEHICLE, controller=0)

    assert settled(board, in_hand).power == 3
    assert settled(board, in_graveyard).toughness == 3


# ---------------------------------------------------------------------------
# CR 306.5: loyalty
# ---------------------------------------------------------------------------


def test_loyalty_on_the_battlefield_is_the_loyalty_counters(board):
    """CR 306.5c. Printed 4, but this one has been attacked down to 1."""
    walker = board.play(PLANESWALKER, controller=0)
    walker.add_counters("loyalty", 1)

    assert board.game.printed_characteristics(walker).loyalty == 4  # what the card says
    assert loyalty(walker, board.chars(walker)) == 1


def test_loyalty_off_the_battlefield_is_the_printed_number(board):
    """CR 306.5a."""
    in_hand = board.hand(PLANESWALKER, controller=0)

    assert loyalty(in_hand, board.chars(in_hand)) == 4


def test_a_permanent_that_is_not_a_planeswalker_has_no_loyalty(board):
    """CR 306.5: loyalty is a planeswalker's alone. Counters named "loyalty" on
    something else are just counters."""
    bear = board.play("Grizzly Bears", controller=0)
    bear.add_counters("loyalty", 3)

    assert loyalty(bear, board.chars(bear)) is None


# ---------------------------------------------------------------------------
# CR 310.4: defense
# ---------------------------------------------------------------------------


def test_defense_on_the_battlefield_is_the_defense_counters(board):
    """CR 310.4c."""
    battle = board.play(BATTLE, controller=0)
    battle.add_counters("defense", 2)

    assert board.game.printed_characteristics(battle).defense == 3  # printed
    assert defense(battle, board.chars(battle)) == 2


def test_defense_off_the_battlefield_is_the_printed_number(board):
    """CR 310.4a."""
    in_hand = board.hand(BATTLE, controller=0)

    assert defense(in_hand, board.chars(in_hand)) == 3


# ---------------------------------------------------------------------------
# CR 306.5b / 310.4b: what a planeswalker or a battle enters with
# ---------------------------------------------------------------------------


def test_a_planeswalker_enters_with_loyalty_counters(board):
    """CR 306.5b, read off the card it is entering as."""
    card = board.hand(PLANESWALKER, controller=0)

    assert entering_counters(board.chars(card)) == (("loyalty", 4),)


def test_a_battle_enters_with_defense_counters(board):
    """CR 310.4b."""
    card = board.hand(BATTLE, controller=0)

    assert entering_counters(board.chars(card)) == (("defense", 3),)


def test_anything_else_enters_with_nothing(board):
    """A creature has no intrinsic entering counters of its own."""
    card = board.hand("Grizzly Bears", controller=0)

    assert entering_counters(board.chars(card)) == ()


def test_a_planeswalker_put_onto_the_battlefield_gets_its_loyalty(board):
    """CR 306.5b is intrinsic, so it applies however the permanent arrives.

    It used to run only for a resolving permanent spell, so a reanimated
    planeswalker entered with zero loyalty and the state-based actions put it
    straight back into the graveyard.
    """
    from mtgfish.rules.kernel.enums import Zone

    card = board.graveyard(PLANESWALKER, controller=0)
    walker = board.game.move_object(card, Zone.BATTLEFIELD, to_player=0)

    assert walker.counter_count("loyalty") == 4


# ---------------------------------------------------------------------------
# What the engine itself still answers
# ---------------------------------------------------------------------------


def test_the_engine_reports_no_power_for_an_uncrewed_vehicle(board):
    """CR 301.7a, end to end through the layer system."""
    vehicle = board.play(VEHICLE, controller=0)

    assert board.pt(vehicle) == (None, None)


def test_a_loyalty_filter_reads_the_counters_not_the_printing(board):
    """CR 306.5c, where it is observable: "target planeswalker with loyalty 3
    or less" has to read the board.

    ``Characteristics.loyalty`` stays the printed number on purpose - CR 306.5b
    needs it that way, to know what the permanent is entering *with* at a
    moment when it has no counters at all. So the rule is applied by whoever
    asks about a permanent's loyalty, and a filter is the caller that matters.
    """
    from mtgfish.rules.kernel.matching import matches
    from mtgfish.rules.kernel.query import NumericConstraint, ObjectFilter

    walker = board.play(PLANESWALKER, controller=0)
    walker.counters.clear()
    walker.add_counters("loyalty", 1)

    at_most_three = ObjectFilter(loyalty=NumericConstraint.at_most(3))
    assert matches(board.game, walker, at_most_three)

    walker.counters.clear()
    walker.add_counters("loyalty", 9)
    assert not matches(board.game, walker, at_most_three)
