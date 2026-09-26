"""Delayed triggered abilities that refer to particular objects (CR 603.7c).

"Return it", "destroy that creature at the beginning of the next end step" -
the delayed ability is made by a resolving effect and must go on meaning the
objects that effect was talking about, however long it waits. It follows the
one move that triggered it and no other: an object that left the zone it was
expected in, or left and came back, is a new object (CR 400.7) and out of the
ability's reach.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build
from mtgfish.rules.kernel.enums import CardType, Step, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def run(board, effects, *, targets=()):
    resolution = Resolution(
        game=board.game, source=0, controller=PlayerId(0), targets=targets
    )
    execute(resolution, effects)
    board.refresh()
    return resolution


def current(board, obj):
    while obj.superseded_by:
        obj = board.game.objects[obj.superseded_by]
    return obj


def earthbend(board, land):
    run(board, build("Earthbend", amount=2), targets=((land.id,),))


# ---------------------------------------------------------------------------
# Earthbend: "when that land dies or is put into exile, return it"
# ---------------------------------------------------------------------------


def test_the_delayed_trigger_carries_the_land_it_was_made_about(board):
    land = board.play("Forest")
    earthbend(board, land)
    assert board.game.delayed_triggers[0].remembered == (land.id,)


def test_the_land_returns_tapped_and_as_a_new_object(board):
    land = board.play("Forest")
    earthbend(board, land)
    actions.destroy(board.game, land, source=0)
    board.settle()
    board.resolve_stack()

    back = current(board, land)
    assert back.zone is Zone.BATTLEFIELD
    assert back.tapped
    # CR 400.7: the animation and the counters belonged to the old object.
    assert not board.chars(back).type_line.has_type(CardType.CREATURE)
    assert back.counter_count("+1/+1") == 0


def test_the_land_returns_from_exile_too(board):
    land = board.play("Forest")
    earthbend(board, land)
    actions.exile(board.game, land, source=0)
    board.settle()
    board.resolve_stack()
    assert current(board, land).zone is Zone.BATTLEFIELD


def test_another_land_dying_does_not_trigger_it(board):
    """A control on the subject: before the trigger was bound to the land it
    was made about, "that land" matched any land that died."""
    land = board.play("Forest")
    other = board.play("Forest")
    earthbend(board, land)
    actions.destroy(board.game, other, source=0)
    board.settle()
    assert not board.game.stack
    assert len(board.game.delayed_triggers) == 1


def test_a_land_that_left_the_graveyard_is_not_returned(board):
    """CR 603.7c: it is no longer in the zone the ability expects, so the
    ability does not affect the card wherever it has gone."""
    land = board.play("Forest")
    earthbend(board, land)
    actions.destroy(board.game, land, source=0)
    board.settle()
    in_graveyard = current(board, land)
    board.game.move_object(in_graveyard, Zone.EXILE)
    board.resolve_stack()
    assert current(board, land).zone is Zone.EXILE
    assert "Forest" not in board.alive(0)


# ---------------------------------------------------------------------------
# "Destroy it at the beginning of the next end step"
# ---------------------------------------------------------------------------

AT_END_STEP = TriggerCondition(
    event_kinds=frozenset({EventKind.STEP_BEGAN}),
    steps=frozenset({int(Step.END_STEP)}),
    functions_in=frozenset(Zone),
    text="at the beginning of the next end step",
)


def tap_then_destroy_at_end(target_id):
    return (
        Effect(
            EffectKind.TAP,
            targets=ObjectFilter(specific=(target_id,)),
            text="tap it",
        ),
        Effect(
            EffectKind.DELAYED_TRIGGER,
            trigger=AT_END_STEP,
            children=(
                Effect(
                    EffectKind.DESTROY,
                    targets=ObjectFilter(remembered=True),
                    text="destroy it",
                ),
            ),
        ),
    )


def end_step(board):
    board.game.emit(
        Event(EventKind.STEP_BEGAN, player=PlayerId(0), amount=int(Step.END_STEP))
    )
    board.settle()
    board.resolve_stack()


def test_it_means_the_creature_the_effect_acted_on(board):
    bears = board.play("Grizzly Bears")
    bystander = board.play("Grizzly Bears")
    run(board, tap_then_destroy_at_end(bears.id))
    end_step(board)
    assert not bears.is_live
    assert current(board, bears).zone is Zone.GRAVEYARD
    assert bystander.is_permanent


def test_a_creature_that_left_and_came_back_is_a_new_object(board):
    """CR 603.7c's note and CR 400.7: the flickered creature is not "it"."""
    bears = board.play("Grizzly Bears")
    run(board, tap_then_destroy_at_end(bears.id))
    exiled = actions.exile(board.game, bears, source=0)
    returned = board.game.move_object(exiled, Zone.BATTLEFIELD)
    board.settle()
    end_step(board)
    assert returned.is_permanent
    assert board.alive(0) == ["Grizzly Bears"]


def test_it_means_what_the_creature_became_in_the_same_resolution(board):
    """"Exile it. Return it at the beginning of the next end step" - by the
    time the delayed ability is made, "it" is the card in exile."""
    bears = board.play("Grizzly Bears")
    effects = (
        Effect(EffectKind.EXILE, targets=ObjectFilter(specific=(bears.id,))),
        Effect(
            EffectKind.DELAYED_TRIGGER,
            trigger=AT_END_STEP,
            children=(
                Effect(
                    EffectKind.PUT_ONTO_BATTLEFIELD,
                    targets=ObjectFilter(remembered=True, zones=frozenset({Zone.EXILE})),
                    text="return it",
                ),
            ),
        ),
    )
    run(board, effects)
    exiled = current(board, bears)
    assert board.game.delayed_triggers[0].remembered == (exiled.id,)
    end_step(board)
    assert current(board, bears).zone is Zone.BATTLEFIELD
