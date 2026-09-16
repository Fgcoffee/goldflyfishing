"""Two Aetherdrift mechanics, both of which the engine got wrong.

Speed did not exist at all - the parser was correctly refusing to invent it.
Station existed but was lumped in with the generic "add counters" keywords,
which got its cost, its counter kind and its amount all wrong at once.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.player import MAX_SPEED
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


# ---------------------------------------------------------------------------
# Speed (CR 702.183)
# ---------------------------------------------------------------------------


def test_start_your_engines_sets_speed_to_one(box):
    """It uses the stack like any other enters trigger (CR 603.3b), so the
    speed is not set until the trigger resolves."""
    assert box.game.player(0).speed == 0
    box.put("Mendicant Core, Guidelight", "battlefield", 0)
    box.settle()
    assert box.game.player(0).speed == 0, "not until it resolves"
    box.resolve_top()
    assert box.game.player(0).speed == 1


def test_starting_twice_does_not_advance_speed(box):
    """Several permanents each say it; the second must not disturb a speed
    that is already running."""
    player = box.game.player(0)
    player.start_engines()
    player.speed = 3
    assert player.start_engines() is False
    assert player.speed == 3


def test_speed_increases_when_an_opponent_loses_life_on_your_turn(box):
    from mtgfish.rules import actions

    box.game.player(0).start_engines()
    actions.lose_life(box.game, box.game.player(1).id, 2)
    assert box.game.player(0).speed == 2


def test_speed_increases_at_most_once_each_turn(box):
    from mtgfish.rules import actions

    box.game.player(0).start_engines()
    actions.lose_life(box.game, box.game.player(1).id, 2)
    actions.lose_life(box.game, box.game.player(1).id, 2)
    assert box.game.player(0).speed == 2, "twice in one turn is once"


def test_your_own_life_loss_does_not_increase_your_speed(box):
    from mtgfish.rules import actions

    box.game.player(0).start_engines()
    actions.lose_life(box.game, box.game.player(0).id, 5)
    assert box.game.player(0).speed == 1


def test_speed_never_passes_the_maximum(box):
    player = box.game.player(0)
    player.start_engines()
    for _ in range(10):
        player.speed_increased_this_turn = False
        player.increase_speed()
    assert player.speed == MAX_SPEED


def test_a_player_who_never_started_gains_no_speed(box):
    from mtgfish.rules import actions

    actions.lose_life(box.game, box.game.player(1).id, 2)
    assert box.game.player(0).speed == 0


def test_a_max_speed_ability_is_gated_on_speed(card_db):
    """The gate is the whole point: without it the ability works from turn
    one, which is a strictly better card than the one printed."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.query import ConditionKind

    card = card_db.lookup("Mendicant Core, Guidelight")
    gated = [
        a
        for face in parse_card(card).faces
        for a in face.abilities
        if a.trigger is not None
        and a.trigger.intervening_if.kind is ConditionKind.AT_MAX_SPEED
    ]
    assert gated, "the Max speed ability must carry the gate"


def test_the_whole_card_now_reads(card_db):
    from mtgfish.parser import parse_card

    assert parse_card(card_db.lookup("Mendicant Core, Guidelight")).fully_parsed


# ---------------------------------------------------------------------------
# Station (CR 721)
# ---------------------------------------------------------------------------


def test_station_taps_another_creature_and_counts_its_power(box):
    """All three of these were wrong: the cost was empty, the counters were
    +1/+1 instead of charge, and the amount was 1 instead of the power."""
    box.put("Wedgelight Rammer", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)  # 4/4

    action = next(a for a in box.legal() if "Station" in a["description"])
    assert not box.perform(action["index"]).get("error")
    box.resolve_top()

    board = {o["name"]: o for o in box.state()["players"][0]["battlefield"]}
    assert board["Serra Angel"]["tapped"] is True, "the cost taps the creature"
    assert board["Wedgelight Rammer"]["tapped"] is False, "not the Spacecraft"
    assert board["Wedgelight Rammer"]["counters"] == {"charge": 4}


def test_station_works_with_a_summoning_sick_creature(box):
    """CR 302.6 restricts only the {T} symbol in a permanent's *own* cost, so
    a creature that entered this turn can still be tapped to pay for this."""
    box.put("Wedgelight Rammer", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)
    for obj in box.game.objects.values():
        if obj.card is not None and obj.card.name == "Serra Angel":
            obj.summoning_sick = True

    action = next(a for a in box.legal() if "Station" in a["description"])
    assert not box.perform(action["index"]).get("error")


def test_station_needs_a_creature_to_tap(box):
    box.put("Wedgelight Rammer", "battlefield", 0)
    assert not any("Station" in a["description"] for a in box.legal())
