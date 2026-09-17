"""How long a continuous effect or a prohibition lasts (CR 611.2b, 514.2).

Only ``END_OF_TURN`` was ever honoured. Every other duration the parser can
read - "until end of combat", "until your next turn", "until the end of your
next turn" - was recorded on the effect and then consulted by nothing, so
those effects behaved as though they had no duration at all.

Three separate cards' worth of consequences, all from the same gap:

  * A creature stolen "until end of turn" stayed stolen, because the control
    change was written onto the object once and layer 2 never recomputed it.
  * "Creatures can't block this turn" locked those creatures out of blocking
    for the rest of the game, because ``_do_restriction`` dropped the
    duration on the floor when it registered the prohibition.
  * "Doesn't untap during its controller's untap step" did nothing, because
    the untap step untapped every tapped permanent without asking.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import Duration
from mtgfish.rules.game import ContinuousEffect
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.layers import Layer
from mtgfish.rules.query import ObjectFilter
from mtgfish.rules.restrictions import (
    Act,
    Restriction,
    prohibited,
    register_standing,
)
from mtgfish.rules.turn import (
    TurnOptions,
    _clear_damage_and_expire_effects,
    _end_of_combat,
    _untap_step,
    take_turn,
)
from mtgfish.rules.values import Value


def _pump(board, obj, duration, controller=0):
    """A +3/+3 that lasts for ``duration``, as a resolved spell would leave it."""
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.MODIFY_PT,
                amount=Value(constant=3),
                amount2=Value(constant=3),
                targets=ObjectFilter(specific=(obj.id,)),
            ),
            source=obj.id,
            controller=PlayerId(controller),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.PT_MODIFY),
            duration=int(duration),
            created_turn=board.game.turn,
        )
    )
    board.game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# Durations on continuous effects
# ---------------------------------------------------------------------------


def test_until_end_of_combat_ends_with_combat(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.END_OF_COMBAT)
    assert board.pt(bear) == (5, 5)

    _end_of_combat(board.game)
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_until_end_of_combat_does_not_wait_for_cleanup(card_db):
    """CR 511.3: it ends in the end of combat step, not at end of turn.

    The difference is the whole postcombat main phase, which is where the
    creature would still have been a 5/5.
    """
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.END_OF_COMBAT)
    _end_of_combat(board.game)
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_until_end_of_turn_still_ends_at_cleanup(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.END_OF_TURN)
    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_an_effect_with_no_duration_never_expires(card_db):
    """CR 611.2b: no stated duration means it lasts indefinitely."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.PERMANENT)
    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    assert board.pt(bear) == (5, 5)


def test_until_your_next_turn_survives_the_opponents_turn(card_db):
    board = make_board(card_db)
    board.game.active_player = PlayerId(0)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.YOUR_NEXT_TURN, controller=0)

    board.game.active_player = PlayerId(1)
    take_turn(board.game, TurnOptions())
    board.refresh()
    assert board.pt(bear) == (5, 5)


def test_until_your_next_turn_ends_as_that_turn_begins(card_db):
    board = make_board(card_db)
    board.game.active_player = PlayerId(0)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.YOUR_NEXT_TURN, controller=0)

    board.game.active_player = PlayerId(1)
    take_turn(board.game, TurnOptions())
    board.game.active_player = PlayerId(0)
    take_turn(board.game, TurnOptions())
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_until_the_end_of_your_next_turn_outlasts_your_current_one(card_db):
    """Made on your own turn, it runs through the *following* one of yours."""
    board = make_board(card_db)
    board.game.active_player = PlayerId(0)
    bear = board.play("Grizzly Bears")
    _pump(board, bear, Duration.END_OF_YOUR_NEXT_TURN, controller=0)

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    assert board.pt(bear) == (5, 5)

    board.game.active_player = PlayerId(1)
    take_turn(board.game, TurnOptions())
    board.game.active_player = PlayerId(0)
    take_turn(board.game, TurnOptions())
    board.refresh()
    assert board.pt(bear) == (2, 2)


# ---------------------------------------------------------------------------
# Control changes (CR 613.1b) end with their effect
# ---------------------------------------------------------------------------


def _steal(board, obj, duration, thief=0):
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.GAIN_CONTROL, targets=ObjectFilter(specific=(obj.id,))
            ),
            source=obj.id,
            controller=PlayerId(thief),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.CONTROL),
            duration=int(duration),
            created_turn=board.game.turn,
        )
    )
    board.game.invalidate_characteristics()
    board.chars(obj)


def test_gaining_control_until_end_of_turn_hands_it_back(card_db):
    """Act of Treason is a loan, not a gift."""
    board = make_board(card_db)
    stolen = board.play("Grizzly Bears", controller=1)
    assert stolen.controller == PlayerId(1)

    _steal(board, stolen, Duration.END_OF_TURN, thief=0)
    assert stolen.controller == PlayerId(0)

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    board.chars(stolen)
    assert stolen.controller == PlayerId(1)


def test_gaining_control_with_no_duration_keeps_it(card_db):
    """Mind Control has no duration, so the creature does not go back."""
    board = make_board(card_db)
    stolen = board.play("Grizzly Bears", controller=1)
    _steal(board, stolen, Duration.PERMANENT, thief=0)

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    board.chars(stolen)
    assert stolen.controller == PlayerId(0)


def test_a_creature_handed_back_is_summoning_sick_again(card_db):
    """CR 302.6: it has not been controlled continuously since the turn began."""
    board = make_board(card_db)
    stolen = board.play("Grizzly Bears", controller=1)
    _steal(board, stolen, Duration.END_OF_TURN, thief=0)
    stolen.summoning_sick = False

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    board.chars(stolen)
    assert stolen.summoning_sick


# ---------------------------------------------------------------------------
# Prohibitions from resolved spells
# ---------------------------------------------------------------------------


def test_a_this_turn_prohibition_ends_at_cleanup(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    register_standing(
        board.game,
        Restriction(
            act=Act.BLOCK,
            duration=int(Duration.END_OF_TURN),
            text="creatures can't block this turn",
        ),
    )
    assert prohibited(board.game, Act.BLOCK, obj=bear) is not None

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    assert prohibited(board.game, Act.BLOCK, obj=bear) is None


def test_a_prohibition_with_no_duration_stays(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    register_standing(board.game, Restriction(act=Act.BLOCK, text="can't block"))

    _clear_damage_and_expire_effects(board.game)
    board.refresh()
    assert prohibited(board.game, Act.BLOCK, obj=bear) is not None


# ---------------------------------------------------------------------------
# The untap step asks before untapping (CR 502.2)
# ---------------------------------------------------------------------------


def _board_with_a_locked_bear(card_db, act):
    scripts = ScriptedAbilities()
    board = make_board(card_db, scripts)
    locked = board.play("Grizzly Bears", tapped=True)
    free = board.play("Savannah Lions", tapped=True)
    scripts.add(
        "Grizzly Bears",
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.RESTRICTION,
                    restrictions=(Restriction(act=act, text="doesn't untap"),),
                ),
            ),
            text="Grizzly Bears doesn't untap during your untap step.",
        ),
    )
    board.refresh()
    board.game.active_player = PlayerId(0)
    _untap_step(board.game)
    return locked, free


@pytest.mark.parametrize("act", [Act.UNTAP_DURING_UNTAP_STEP, Act.UNTAP])
def test_a_permanent_that_doesnt_untap_stays_tapped(card_db, act):
    """Both wordings. UNTAP_DURING_UNTAP_STEP is Sleep and the tap-down auras;
    a plain UNTAP prohibition is how exert is modelled."""
    locked, _ = _board_with_a_locked_bear(card_db, act)
    assert locked.tapped


@pytest.mark.parametrize("act", [Act.UNTAP_DURING_UNTAP_STEP, Act.UNTAP])
def test_everything_else_still_untaps(card_db, act):
    _, free = _board_with_a_locked_bear(card_db, act)
    assert not free.tapped


def test_an_unrestricted_board_untaps_completely(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears", tapped=True)
    board.game.active_player = PlayerId(0)
    _untap_step(board.game)
    assert not bear.tapped
