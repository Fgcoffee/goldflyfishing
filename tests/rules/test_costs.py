"""Costs (CR 118).

Alternative costs, additional costs, unpayable costs, and the reduction rules
that are more subtle than "subtract a number". Each of these changes what a
real deck can do, and each has a shape that a naive implementation gets wrong
in a specific way noted on the test.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.casting import CastError, cast_spell, compute_total_cost
from mtgfish.rules.costs import AdditionalCost, AlternativeCost, Cost, CostComponent, CostKind
from mtgfish.rules.enums import CardType, Phase, Step, Zone
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.legality import legal_actions
from mtgfish.rules.mana import ManaCost
from mtgfish.rules.priority import Action, ActionKind
from mtgfish.rules.query import ControllerRelation, ObjectFilter, Value

from harness import ScriptedAbilities, make_board

CREATURES_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def give_mana(board, amount: int, color: str = "G", player: int = 0):
    from mtgfish.rules.enums import LETTER_TO_COLOR
    from mtgfish.rules.mana import ManaKind

    board.game.player(PlayerId(player)).mana_pool.add(LETTER_TO_COLOR[color], 0)
    board.game.player(PlayerId(player)).mana_pool.add(
        ManaKind(LETTER_TO_COLOR[color]), amount
    )


# ---------------------------------------------------------------------------
# CR 118.7 - reduction by specific mana
# ---------------------------------------------------------------------------


def test_generic_reduction_only_touches_generic():
    """CR 118.7a."""
    assert str(ManaCost.parse("{4}{G}{G}").reduced_by(2)) == "{2}{G}{G}"


def test_coloured_reduction_removes_that_colour():
    cost = ManaCost.parse("{2}{G}{G}").reduced_by_mana(ManaCost.parse("{G}"))
    assert str(cost) == "{2}{G}"


def test_coloured_reduction_with_no_such_colour_reduces_generic():
    """CR 118.7b: a {B} reduction against a cost with no black reduces generic."""
    cost = ManaCost.parse("{2}{G}{G}").reduced_by_mana(ManaCost.parse("{B}"))
    assert str(cost) == "{1}{G}{G}"


def test_excess_coloured_reduction_spills_into_generic():
    """CR 118.7c: more {G} than the cost has means the rest comes off generic."""
    cost = ManaCost.parse("{3}{G}").reduced_by_mana(ManaCost.parse("{G}{G}"))
    assert str(cost) == "{2}"


def test_colourless_reduction_prefers_a_colourless_symbol():
    """CR 118.7d."""
    cost = ManaCost.parse("{2}{C}").reduced_by_mana(ManaCost.parse("{C}"))
    assert str(cost) == "{2}"


def test_snow_reduction_reduces_generic():
    """CR 118.7g."""
    cost = ManaCost.parse("{3}{U}").reduced_by_mana(ManaCost.parse("{S}"))
    assert str(cost) == "{2}{U}"


def test_reduction_never_goes_below_zero():
    assert str(ManaCost.parse("{1}{W}").reduced_by_mana(ManaCost.parse("{W}{W}{W}"))) == ""


# ---------------------------------------------------------------------------
# CR 118.6 - unpayable costs
# ---------------------------------------------------------------------------


def test_a_card_with_no_mana_cost_cannot_be_cast(board):
    """CR 118.6: no mana cost is an *unpayable* cost, not a cost of zero."""
    game = board.game
    ancestral = board.hand("Ancestral Vision", controller=0)  # no mana cost
    give_mana(board, 10, "U")

    with pytest.raises(CastError, match="unpayable"):
        cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=ancestral.id))


def test_such_a_card_is_not_offered_as_a_legal_action(board):
    card = board.hand("Ancestral Vision", controller=0)
    give_mana(board, 10, "U")
    assert not [
        a
        for a in legal_actions(board.game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def test_an_alternative_cost_makes_it_payable(board):
    """CR 118.6a: only an alternative cost rescues an unpayable one."""
    game = board.game
    board.scripts.add(
        "Ancestral Vision",
        Ability(
            AbilityKind.STATIC,
            alternative_cost=AlternativeCost(
                cost=Cost.mana("{U}"), keyword="Suspend-ish", text="pay {U} instead"
            ),
            text="alternative cost",
        ),
    )
    card = board.hand("Ancestral Vision", controller=0)
    give_mana(board, 3, "U")

    spell = cast_spell(
        game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, alternative_cost=0),
    )
    assert spell.zone is Zone.STACK
    assert game.player(PlayerId(0)).mana_pool.total == 2


# ---------------------------------------------------------------------------
# CR 118.9 - alternative costs
# ---------------------------------------------------------------------------


def flashback(cost: str) -> Ability:
    return Ability(
        AbilityKind.STATIC,
        alternative_cost=AlternativeCost(
            cost=Cost.mana(cost),
            from_zone=Zone.GRAVEYARD,
            keyword="Flashback",
            text=f"Flashback {cost}",
        ),
        functions_in=frozenset({Zone.GRAVEYARD}),
        text=f"Flashback {cost}",
    )


def test_an_alternative_cost_replaces_the_mana_cost(board):
    game = board.game
    board.scripts.add("Lightning Bolt", flashback("{3}{R}"))
    card = board.graveyard("Lightning Bolt", controller=0)

    total = compute_total_cost(
        game, card, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id, alternative_cost=0)
    )
    assert str(total.final_mana()) == "{3}{R}"
    assert total.is_alternative


def test_the_printed_mana_cost_is_unchanged(board):
    """CR 118.9c: anything asking for the mana cost still sees the real one."""
    board.scripts.add("Lightning Bolt", flashback("{3}{R}"))
    card = board.graveyard("Lightning Bolt", controller=0)
    assert str(board.game.characteristics(card).mana_cost) == "{R}"


def test_flashback_makes_a_graveyard_card_castable(board):
    board.scripts.add("Lightning Bolt", flashback("{R}"))
    board.graveyard("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")

    casts = [
        a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL
    ]
    assert casts
    assert all(a.alternative_cost == 0 for a in casts)


def test_a_graveyard_card_without_flashback_is_not_castable(board):
    board.graveyard("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")
    assert not [
        a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL
    ]


def test_a_hand_card_cannot_use_a_graveyard_only_alternative(board):
    """Flashback is not a discount on a card in hand."""
    board.scripts.add("Lightning Bolt", flashback("{R}"))
    board.hand("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")

    casts = [
        a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL
    ]
    assert all(a.alternative_cost == -1 for a in casts)


def test_cost_increases_still_apply_to_an_alternative_cost(board):
    """CR 118.9d: an alternative cost is not a way to dodge a tax."""
    from mtgfish.rules.effects import Effect, EffectKind

    game = board.game
    board.scripts.add("Lightning Bolt", flashback("{R}"))
    board.scripts.add(
        "Thalia, Guardian of Thraben",
        Ability.static(
            Effect(
                EffectKind.MODIFY_COST,
                targets=ObjectFilter(zones=frozenset({Zone.STACK, Zone.GRAVEYARD})),
                amount=Value.of(1),
            ),
            text="spells cost {1} more",
        ),
    )
    card = board.graveyard("Lightning Bolt", controller=0)
    board.play("Thalia, Guardian of Thraben", controller=1)

    total = compute_total_cost(
        game, card, PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, alternative_cost=0),
    )
    assert total.final_mana().mana_value == 2, "{R} plus the {1} tax"


# ---------------------------------------------------------------------------
# CR 118.8 - additional costs
# ---------------------------------------------------------------------------


def kicker(cost: str) -> Ability:
    return Ability(
        AbilityKind.STATIC,
        additional_cost=AdditionalCost(
            cost=Cost.mana(cost), optional=True, keyword="Kicker", text=f"Kicker {cost}"
        ),
        text=f"Kicker {cost}",
    )


def sacrifice_a_creature() -> Ability:
    return Ability(
        AbilityKind.STATIC,
        additional_cost=AdditionalCost(
            cost=Cost(
                (
                    CostComponent(
                        CostKind.SACRIFICE,
                        amount=Value.of(1),
                        filter=CREATURES_YOU_CONTROL,
                        text="sacrifice a creature",
                    ),
                )
            ),
            text="As an additional cost, sacrifice a creature.",
        ),
        text="additional cost",
    )


def test_a_mandatory_additional_cost_is_always_added(board):
    game = board.game
    board.scripts.add("Lightning Bolt", sacrifice_a_creature())
    card = board.hand("Lightning Bolt", controller=0)

    total = compute_total_cost(
        game, card, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id)
    )
    assert any(c.kind is CostKind.SACRIFICE for c in total.additional)


def test_an_optional_additional_cost_is_only_added_when_chosen(board):
    game = board.game
    board.scripts.add("Lightning Bolt", kicker("{2}"))
    card = board.hand("Lightning Bolt", controller=0)

    unkicked = compute_total_cost(
        game, card, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id)
    )
    assert unkicked.final_mana().mana_value == 1

    kicked = compute_total_cost(
        game, card, PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, additional_costs=(0,)),
    )
    assert kicked.final_mana().mana_value == 3


def test_paying_a_sacrifice_additional_cost_really_sacrifices(board):
    game = board.game
    board.scripts.add("Lightning Bolt", sacrifice_a_creature())
    card = board.hand("Lightning Bolt", controller=0)
    victim = board.play("Grizzly Bears", controller=0)
    board.play("Grizzly Bears", controller=1)  # An opponent's, which must not be taken.
    give_mana(board, 3, "R")

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))

    assert victim.id not in game.battlefield
    assert "Grizzly Bears" in board.in_graveyard(0)


def test_an_unpayable_additional_cost_rewinds_the_whole_cast(board):
    """CR 118.3 and CR 601.2h together: no creature, so nothing happens at all."""
    game = board.game
    board.scripts.add("Lightning Bolt", sacrifice_a_creature())
    card = board.hand("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")
    hand_before = len(game.player(PlayerId(0)).hand)

    with pytest.raises(CastError):
        cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))

    assert not game.stack
    assert len(game.player(PlayerId(0)).hand) == hand_before
    assert game.player(PlayerId(0)).mana_pool.total == 3, "no mana was spent"


def test_additional_costs_do_not_change_the_mana_cost(board):
    """CR 118.8d."""
    board.scripts.add("Lightning Bolt", kicker("{2}"))
    card = board.hand("Lightning Bolt", controller=0)
    assert str(board.game.characteristics(card).mana_cost) == "{R}"


# ---------------------------------------------------------------------------
# Non-mana costs (CR 118.3)
# ---------------------------------------------------------------------------


def test_paying_life_as_a_cost(board):
    game = board.game
    cost = Cost((CostComponent(CostKind.PAY_LIFE, amount=Value.of(5)),))
    board.scripts.add(
        "Lightning Bolt",
        Ability(AbilityKind.STATIC, additional_cost=AdditionalCost(cost=cost)),
    )
    card = board.hand("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.player(PlayerId(0)).life == 35


def test_not_enough_life_rewinds(board):
    game = board.game
    cost = Cost((CostComponent(CostKind.PAY_LIFE, amount=Value.of(100)),))
    board.scripts.add(
        "Lightning Bolt",
        Ability(AbilityKind.STATIC, additional_cost=AdditionalCost(cost=cost)),
    )
    card = board.hand("Lightning Bolt", controller=0)
    give_mana(board, 3, "R")

    with pytest.raises(CastError):
        cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.player(PlayerId(0)).life == 40


def test_discarding_as_a_cost(board):
    game = board.game
    cost = Cost((CostComponent(CostKind.DISCARD, amount=Value.of(1)),))
    board.scripts.add(
        "Lightning Bolt",
        Ability(AbilityKind.STATIC, additional_cost=AdditionalCost(cost=cost)),
    )
    card = board.hand("Lightning Bolt", controller=0)
    board.hand("Forest", controller=0)
    give_mana(board, 3, "R")
    hand_before = len(game.player(PlayerId(0)).hand)

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    # One card left the hand to be cast, one more was discarded.
    assert len(game.player(PlayerId(0)).hand) == hand_before - 2


def test_zero_is_a_real_cost_that_must_still_be_paid(board):
    """CR 118.5a: a {0} spell does not cast itself."""
    game = board.game
    card = board.hand("Ancestral Vision", controller=0)
    assert not [
        a for a in legal_actions(game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]
