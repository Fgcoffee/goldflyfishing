"""Triggered abilities (CR 603) and replacement effects (CR 614, 615, 616).

The two ways a card reacts to something happening, and the difference between
them is the whole point: a trigger fires *after* the event and uses the stack; a
replacement changes the event *before* it happens and uses nothing.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import add_counters, deal_damage, destroy
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import put_triggers_on_stack
from mtgfish.rules.cr600_spells_and_abilities.cr614_replacement import (
    ReplacementEffect,
    ReplacementKind,
    register,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    YOU,
    Comparison,
    Condition,
    ConditionKind,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    Value,
)

CREATURES = ObjectFilter(types_all=CardType.CREATURE)


def draw_on_creature_death(*, intervening_if=None) -> Ability:
    """"Whenever a creature dies, draw a card." """
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.DIES}),
            subject=CREATURES,
            intervening_if=intervening_if or Condition(ConditionKind.ALWAYS),
            text="whenever a creature dies",
        ),
        Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card"),
        text="Whenever a creature dies, draw a card.",
    )


def etb_gain_life() -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=ObjectFilter(
                types_all=CardType.CREATURE, controller=ControllerRelation.YOU
            ),
            text="whenever a creature you control enters",
        ),
        Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2)),
        text="Whenever a creature you control enters, gain 2 life.",
    )


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


# ---------------------------------------------------------------------------
# Triggering (CR 603.2, 603.3)
# ---------------------------------------------------------------------------


def test_a_trigger_fires_on_its_event(board):
    board.scripts.add("Blood Artist", draw_on_creature_death())
    board.play("Blood Artist", controller=0)
    bears = board.play("Grizzly Bears", controller=0)

    destroy(board.game, bears)
    assert board.game.pending_triggers


def test_a_trigger_waits_for_priority_before_reaching_the_stack(board):
    """CR 603.3b: it triggers now, it goes on the stack later."""
    game = board.game
    board.scripts.add("Blood Artist", draw_on_creature_death())
    board.play("Blood Artist", controller=0)
    bears = board.play("Grizzly Bears", controller=0)

    destroy(game, bears)
    assert not game.stack  # Noted, not yet on the stack.

    put_triggers_on_stack(game)
    assert game.stack


def test_a_trigger_resolves_and_does_its_thing(board):
    game = board.game
    board.scripts.add("Blood Artist", draw_on_creature_death())
    board.play("Blood Artist", controller=0)
    bears = board.play("Grizzly Bears", controller=0)
    hand_before = game.player(PlayerId(0)).hand_size

    destroy(game, bears)
    board.settle()
    board.resolve_stack()

    assert game.player(PlayerId(0)).hand_size == hand_before + 1


def test_a_trigger_still_resolves_when_its_source_is_gone(board):
    """CR 603.3d: the ability on the stack exists independently of its source."""
    game = board.game
    board.scripts.add("Blood Artist", draw_on_creature_death())
    artist = board.play("Blood Artist", controller=0)
    bears = board.play("Grizzly Bears", controller=0)
    hand_before = game.player(PlayerId(0)).hand_size

    destroy(game, bears)
    board.settle()
    destroy(game, artist)  # The source leaves while the trigger is on the stack.
    board.resolve_stack()

    assert game.player(PlayerId(0)).hand_size >= hand_before + 1


def test_a_trigger_does_not_fire_on_an_unrelated_event(board):
    board.scripts.add("Blood Artist", draw_on_creature_death())
    board.play("Blood Artist", controller=0)
    board.play("Sol Ring", controller=0)

    destroy(board.game, next(o for o in board.game.permanents(PlayerId(0))
                             if board.game.characteristics(o).name == "Sol Ring"))
    assert not board.game.pending_triggers


def test_controller_filter_is_respected(board):
    """"a creature *you control*" does not fire for an opponent's creature."""
    board.scripts.add("Soul Warden", etb_gain_life())
    board.play("Soul Warden", controller=0)
    board.game.pending_triggers.clear()

    board.play("Grizzly Bears", controller=1)
    from mtgfish.rules.kernel.events import Event

    board.game.emit(
        Event(EventKind.ENTERS_BATTLEFIELD,
              object_id=next(o.id for o in board.game.permanents(PlayerId(1))),
              player=PlayerId(1))
    )
    assert not board.game.pending_triggers


# ---------------------------------------------------------------------------
# Intervening if (CR 603.4)
# ---------------------------------------------------------------------------


def _controls_two_creatures() -> Condition:
    return Condition(
        ConditionKind.OBJECT_COUNT,
        filter=ObjectFilter(
            types_all=CardType.CREATURE, controller=ControllerRelation.YOU
        ),
        constraint=NumericConstraint(Comparison.GE, Value.of(2)),
        text="if you control two or more creatures",
    )


def test_intervening_if_stops_the_trigger_when_false(board):
    """CR 603.4: checked when it would trigger, and it does not."""
    board.scripts.add(
        "Blood Artist", draw_on_creature_death(intervening_if=_controls_two_creatures())
    )
    board.play("Blood Artist", controller=0)
    lone = board.play("Grizzly Bears", controller=0)

    # Killing the only creature leaves the Artist alone, so the condition fails.
    destroy(board.game, lone)
    assert not board.game.pending_triggers


def test_intervening_if_allows_the_trigger_when_true(board):
    board.scripts.add(
        "Blood Artist", draw_on_creature_death(intervening_if=_controls_two_creatures())
    )
    board.play("Blood Artist", controller=0)
    board.play("Grizzly Bears", controller=0)
    victim = board.play("Grizzly Bears", controller=0)

    destroy(board.game, victim)
    assert board.game.pending_triggers


def test_intervening_if_is_checked_again_on_resolution(board):
    """CR 603.4: false at resolution means the ability does nothing.

    This second check is what separates an intervening-if from an ordinary "if"
    inside the effect.
    """
    game = board.game
    board.scripts.add(
        "Blood Artist", draw_on_creature_death(intervening_if=_controls_two_creatures())
    )
    board.play("Blood Artist", controller=0)
    spare = board.play("Grizzly Bears", controller=0)
    victim = board.play("Grizzly Bears", controller=0)

    destroy(game, victim)
    put_triggers_on_stack(game)
    hand_before = game.player(PlayerId(0)).hand_size

    # The condition becomes false while the trigger is on the stack.
    destroy(game, spare)
    game.pending_triggers.clear()
    board.resolve_stack()

    assert game.player(PlayerId(0)).hand_size == hand_before


# ---------------------------------------------------------------------------
# APNAP ordering (CR 101.4, 603.3b)
# ---------------------------------------------------------------------------


def test_simultaneous_triggers_are_ordered_apnap(board):
    game = board.game
    game.active_player = PlayerId(1)
    board.scripts.add("Blood Artist", draw_on_creature_death())
    board.play("Blood Artist", controller=0)
    board.play("Blood Artist", controller=1)
    victim = board.play("Grizzly Bears", controller=0)

    destroy(game, victim)
    # The controller each trigger was noted under, which is now recorded on
    # the pending trigger itself rather than looked up again on its source.
    controllers = [pending.controller for pending in game.pending_triggers]
    assert controllers[0] == 1, "the active player's trigger is noted first"


# ---------------------------------------------------------------------------
# Replacement effects (CR 614)
# ---------------------------------------------------------------------------


def test_damage_prevention_stops_the_damage(board):
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            subject=CREATURES,
            amount=0,  # Prevent all of it.
            text="prevent all damage to creatures",
        ),
    )

    dealt = deal_damage(game, bears, 5, source_controller=PlayerId(1))
    assert dealt == 0
    assert bears.damage == 0


def test_partial_prevention_lets_the_rest_through(board):
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            subject=CREATURES,
            amount=1,
            text="prevent 1 damage",
        ),
    )

    deal_damage(game, bears, 3, source_controller=PlayerId(1))
    assert bears.damage == 2


def test_counter_doubling_replaces_rather_than_adding_again(board):
    """CR 614: Doubling Season modifies the event, it does not fire twice."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.MODIFY_COUNTERS,
            event_kinds=frozenset({EventKind.COUNTER_ADDED}),
            subject=CREATURES,
            amount=2,
            text="two extra +1/+1 counters",
        ),
    )

    add_counters(game, bears, "+1/+1", 2)
    assert bears.counter_count("+1/+1") == 4


def test_a_replacement_applies_at_most_once(board):
    """CR 614.5, and the only thing standing between us and an infinite loop."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.MODIFY_COUNTERS,
            event_kinds=frozenset({EventKind.COUNTER_ADDED}),
            subject=CREATURES,
            amount=1,
        ),
    )

    add_counters(game, bears, "+1/+1", 1)
    assert bears.counter_count("+1/+1") == 2


def test_two_replacements_both_apply_once_each(board):
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    for _ in range(2):
        register(
            game,
            ReplacementEffect(
                kind=ReplacementKind.MODIFY_COUNTERS,
                event_kinds=frozenset({EventKind.COUNTER_ADDED}),
                subject=CREATURES,
                amount=1,
            ),
        )

    add_counters(game, bears, "+1/+1", 1)
    assert bears.counter_count("+1/+1") == 3


def test_zone_change_redirect_sends_it_elsewhere(board):
    """"If it would die, exile it instead" - and it never reaches the graveyard."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.REDIRECT_ZONE_CHANGE,
            event_kinds=frozenset({EventKind.ZONE_CHANGE}),
            subject=CREATURES,
            destination=Zone.EXILE,
            text="exile it instead",
        ),
    )

    destroy(game, bears)
    assert not game.player(PlayerId(0)).graveyard
    assert game.exile


def test_a_one_shot_shield_is_used_up(board):
    """CR 615.5: a prevention shield applies once and then is gone."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            subject=CREATURES,
            amount=2,
            one_shot=True,
        ),
    )

    deal_damage(game, bears, 2, source_controller=PlayerId(1))
    assert bears.damage == 0

    deal_damage(game, bears, 2, source_controller=PlayerId(1))
    assert bears.damage == 2  # The shield is spent.


def test_a_prevented_event_produces_no_trigger(board):
    """The observable difference between replacement and triggering.

    Damage that is fully prevented never happened, so nothing that watches for
    damage sees anything at all.
    """
    game = board.game
    game.log.enabled = True
    bears = board.play("Grizzly Bears", controller=0)
    register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=frozenset({EventKind.DAMAGE_DEALT}),
            subject=CREATURES,
            amount=0,
        ),
    )

    before = len(game.log)
    deal_damage(game, bears, 4, source_controller=PlayerId(1))
    emitted = " ".join(e.text for e in game.log.entries[before:])

    assert "DAMAGE_PREVENTED" in emitted
    assert "DAMAGE_DEALT" not in emitted
