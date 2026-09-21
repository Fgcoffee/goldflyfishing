"""The numbers the game uses (CR 107.1, 107.2), where a cost reads them.

Magic counts in integers, never below zero, and an undeterminable number is
zero. The engine enforced the first two by hand at each call site and the
third nowhere - so a cost whose amount came out negative was charged
backwards, and a cost referring to a quality nobody can determine was charged
as free.

The seam is ``required_amount``: every non-mana cost component passes through
it, on the dry run that decides legality and on the payment alike.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr100_game_concepts.cr107_numbers import (
    UNDETERMINABLE,
    Undeterminable,
    chosen_number,
    effect_result,
    is_undeterminable,
)
from mtgfish.rules.cr100_game_concepts.cr118_costs import (
    CostComponent,
    CostKind,
    required_amount,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    CastError,
    _check_payable,
    _pay_component,
)
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import Value, ValueKind


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def minus(left: int, right: int) -> Value:
    """A realistic way for a cost amount to come out negative."""
    return Value(ValueKind.DIFFERENCE, operands=(Value.of(left), Value.of(right)))


# ---------------------------------------------------------------------------
# The numbers themselves
# ---------------------------------------------------------------------------


def test_an_effects_result_is_never_negative():
    """CR 107.1b: a calculation that yields a negative number uses zero."""
    assert effect_result(-3) == 0
    assert effect_result(0) == 0
    assert effect_result(4) == 4


def test_a_chosen_number_is_zero_or_more():
    """CR 107.1c: "any number" is zero or a positive number."""
    assert chosen_number(-1) == 0
    assert chosen_number(0) == 0
    assert chosen_number(7) == 7


def test_an_undeterminable_number_is_zero():
    """CR 107.2: anything that needs an undeterminable number uses 0."""
    assert UNDETERMINABLE == 0
    assert int(UNDETERMINABLE) == 0
    assert str(UNDETERMINABLE) == "0"


def test_an_undeterminable_number_is_still_distinguishable():
    """A cost has to tell it apart from a real zero (CR 903.4f)."""
    assert is_undeterminable(UNDETERMINABLE)
    assert not is_undeterminable(0)
    assert not is_undeterminable(3)


def test_calculating_with_an_undeterminable_number_leaves_an_ordinary_zero():
    """CR 107.2 speaks of a number used as a result *or in a calculation*.

    Once it has been added to something it is simply the zero the rule says to
    use, and nothing downstream should still treat the sum as undefined.
    """
    assert UNDETERMINABLE + 5 == 5
    assert not is_undeterminable(UNDETERMINABLE + 0)


def test_the_marker_can_say_what_was_undefined():
    reason = Undeterminable("a commander's colour identity")
    assert reason == 0
    assert "colour identity" in repr(reason)


# ---------------------------------------------------------------------------
# CR 107.1b where a cost reads it
# ---------------------------------------------------------------------------


def test_a_consuming_cost_is_still_floored_at_one():
    """Unchanged: a cost that consumes something consumes at least one."""
    assert required_amount(CostComponent(CostKind.SACRIFICE), 0) == 1
    assert required_amount(CostComponent(CostKind.SACRIFICE), -2) == 1
    assert required_amount(CostComponent(CostKind.SACRIFICE), 3) == 3


def test_a_negative_cost_amount_becomes_zero():
    """CR 107.1b: a cost is a calculated result, so it never goes below zero."""
    assert required_amount(CostComponent(CostKind.PAY_ENERGY), -3) == 0
    assert required_amount(CostComponent(CostKind.PAY_LIFE), -3) == 0
    assert required_amount(CostComponent(CostKind.PUT_COUNTERS), -3) == 0


def test_a_loyalty_cost_stays_signed():
    """CR 107.7: a negative loyalty symbol means removing that many counters."""
    assert required_amount(CostComponent(CostKind.LOYALTY), -3) == -3
    assert required_amount(CostComponent(CostKind.LOYALTY), 2) == 2


def test_paying_a_negative_energy_cost_does_not_hand_out_energy(board):
    """The bug the clamp exists for: -= a negative amount is a gift."""
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    player.energy = 2
    component = CostComponent(CostKind.PAY_ENERGY, amount=minus(1, 4))

    _pay_component(board.game, PlayerId(0), component, source)

    assert player.energy == 2


def test_a_negative_life_cost_takes_no_life(board):
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    before = player.life
    component = CostComponent(CostKind.PAY_LIFE, amount=minus(0, 5))

    _pay_component(board.game, PlayerId(0), component, source)

    assert player.life == before


def test_a_cost_of_zero_energy_is_payable_with_no_energy(board):
    """CR 118.3b's sibling: a cost of nothing is paid by having nothing."""
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    player.energy = 0
    component = CostComponent(CostKind.PAY_ENERGY, amount=Value.of(0))

    _check_payable(board.game, PlayerId(0), component, source)  # does not raise


# ---------------------------------------------------------------------------
# CR 903.4f / CR 118.6: an undeterminable amount makes the cost unpayable
# ---------------------------------------------------------------------------


def test_an_undeterminable_cost_amount_is_refused():
    """CR 903.4f: a cost referring to an undefined quality is unpayable, and
    CR 118.6 makes attempting to pay an unpayable cost illegal."""
    component = CostComponent(CostKind.PAY_LIFE)
    with pytest.raises(CastError):
        required_amount(component, Undeterminable("a commander's colour identity"))


def test_an_undeterminable_cost_is_refused_even_where_zero_would_be_free():
    """A cost of zero is the most payable cost there is, which is exactly why
    reporting a plain 0 for an undefined quality is the wrong answer."""
    assert required_amount(CostComponent(CostKind.PAY_ENERGY), 0) == 0
    with pytest.raises(CastError):
        required_amount(CostComponent(CostKind.PAY_ENERGY), UNDETERMINABLE)


def test_the_refusal_says_what_was_undefined():
    with pytest.raises(CastError) as caught:
        required_amount(
            CostComponent(CostKind.SACRIFICE),
            Undeterminable("a commander's colour identity"),
        )
    assert "colour identity" in str(caught.value)


def test_a_defined_commander_identity_is_an_ordinary_payable_cost(board):
    """The other side of CR 903.4f: with a commander the quality is defined,
    and a cost referring to it is charged like any other."""
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    assert player.commanders
    component = CostComponent(
        CostKind.PAY_LIFE, amount=Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)
    )

    _check_payable(board.game, PlayerId(0), component, source)  # does not raise


def test_a_cost_referring_to_an_undefined_commander_identity_is_unpayable(board):
    """CR 903.4f end to end: no commander, so the quality is undefined and any
    cost referring to it cannot be paid. A cost of zero would make it free."""
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    player.commanders = ()

    component = CostComponent(
        CostKind.PAY_LIFE, amount=Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)
    )
    with pytest.raises(CastError):
        _check_payable(board.game, PlayerId(0), component, source)


# ---------------------------------------------------------------------------
# CR 107.1b where a mana cost reads it
# ---------------------------------------------------------------------------


def test_a_negative_x_is_not_a_cost_reduction():
    """CR 107.1b: the value chosen for X is zero or more."""
    cost = ManaCost.parse("{X}{G}")
    assert str(cost.substitute_x(-3)) == "{G}"
    assert str(cost.substitute_x(0)) == "{G}"
    assert str(cost.substitute_x(2)) == "{2}{G}"


def test_the_same_cost_is_payable_when_there_is_a_commander(board):
    """The control. CR 903.4f is about the quality being *undefined*, not about
    the cost being zero: a mono-green commander makes this a cost of 1 life,
    which is paid like any other.
    """
    source = board.play("Grizzly Bears", controller=0)
    player = board.game.player(PlayerId(0))
    commander = board.play("Kenrith, the Returned King", controller=0)
    player.commanders = (commander.id,)

    component = CostComponent(
        CostKind.PAY_LIFE, amount=Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)
    )
    _check_payable(board.game, PlayerId(0), component, source)
