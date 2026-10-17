"""CR 714: Sagas, from their oracle text to the graveyard, on a real board.

The chapter machinery was tested with hand-built abilities, which proved the
engine right and said nothing about whether any real Saga reached it: every
chapter line was read as a trigger condition and failed. These run the parsed
card through a Saga's whole life.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    from mtgfish.parser.verdicts import VerdictStore

    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _saga(box, name):
    return next(
        o
        for o in box.game.permanents(0)
        if box.game.characteristics(o).name == name
    )


def _run_stack(box):
    box.settle()
    while box.game.stack:
        box.resolve_top()


def _next_precombat_main(box):
    """A later turn of player 0's, as far as its precombat main phase.

    CR 714.3c: the lore counter is a turn-based action as that phase begins.
    """
    from mtgfish.rules.cr500_turn_structure.cr500_turn import (
        TurnOptions,
        _turn_based_actions,
    )

    box.game.turn += 2
    box.game.active_player = 0
    box.game.phase = Phase.PRECOMBAT_MAIN
    box.game.step = Step.MAIN
    _turn_based_actions(box.game, Step.MAIN, TurnOptions())


def _knights(box):
    return [
        o
        for o in box.game.permanents(0)
        if box.game.characteristics(o).has_subtype("Knight")
    ]


def test_a_saga_goes_through_its_chapters_and_is_sacrificed(box):
    """CR 714.3a, 714.2b, 714.3c, 714.4 in order.

    History of Benalia: I and II each make a Knight, III pumps Knights. It
    enters with a lore counter (chapter I), gains one each precombat main
    (II, then III), and once chapter III has left the stack the state-based
    action sacrifices it.
    """
    box.put("History of Benalia", "battlefield", 0)
    saga = _saga(box, "History of Benalia")
    assert saga.counter_count("lore") == 1
    _run_stack(box)
    assert len(_knights(box)) == 1

    _next_precombat_main(box)
    assert saga.counter_count("lore") == 2
    _run_stack(box)
    assert len(_knights(box)) == 2
    assert saga.zone is Zone.BATTLEFIELD

    _next_precombat_main(box)
    box.settle()
    # CR 714.4: not while its final chapter ability is waiting on the stack.
    assert len(box.game.stack) == 1
    assert saga.zone is Zone.BATTLEFIELD
    box.resolve_top()
    for knight in _knights(box):
        assert box.game.characteristics(knight).power == 4
    assert saga.id not in [o.id for o in box.game.permanents(0)]
    assert "History of Benalia" in [box._name(box.game.objects[i]) for i in box.game.player(0).graveyard]


def test_a_counter_of_another_kind_is_not_a_chapter(box):
    """CR 714.2b is about lore counters only: a +1/+1 counter on a Saga must
    not advance it or replay a chapter."""
    from mtgfish.rules.cr100_game_concepts import actions

    box.put("History of Benalia", "battlefield", 0)
    saga = _saga(box, "History of Benalia")
    _run_stack(box)
    actions.add_counters(box.game, saga, "+1/+1", 1)
    box.settle()
    assert not box.game.stack


def test_read_ahead_skips_the_chapters_before_the_chosen_one(box):
    """CR 714.3b and 702.155: a read-ahead Saga enters with the chosen number
    of lore counters, and only that chapter triggers on the turn it enters."""
    from mtgfish.rules.kernel.ids import PlayerId

    class ReadsAhead:
        def choose_read_ahead(self, game, player, obj, final):
            return final

    box.game.agents[PlayerId(0)] = ReadsAhead()
    box.put("The Elder Dragon War", "battlefield", 0)
    saga = _saga(box, "The Elder Dragon War")
    assert saga.counter_count("lore") == 3
    box.settle()
    assert len(box.game.stack) == 1  # chapter III only
    box.resolve_top()
    dragons = [
        o
        for o in box.game.permanents(0)
        if box.game.characteristics(o).has_subtype("Dragon")
        and o is not saga
    ]
    assert len(dragons) == 1
    assert saga.id not in [o.id for o in box.game.permanents(0)]


def test_read_ahead_left_at_one_is_an_ordinary_saga(box):
    box.put("The Elder Dragon War", "battlefield", 0)
    saga = _saga(box, "The Elder Dragon War")
    assert saga.counter_count("lore") == 1
    box.settle()
    assert len(box.game.stack) == 1  # chapter I
