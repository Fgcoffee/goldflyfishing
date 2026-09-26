"""Continuous effects that reach cards outside the battlefield (CR 611.2c, 611.3a).

An effect modifies whatever it says it affects, in whichever zone that is: a
resolved "each card exiled this way may be cast for {2}" gives the exiled card
an ability, and a static "creature cards in your graveyard have flying" speaks
of cards in a graveyard. Granted abilities then function where the card is
(CR 113.6), which for an alternative cost means the card can be cast
(CR 118.9, 601.2b).
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build
from mtgfish.rules.kernel.enums import CardType, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def current(board, obj):
    while obj.superseded_by:
        obj = board.game.objects[obj.superseded_by]
    return obj


def airbend(board, obj):
    execute(
        Resolution(game=board.game, source=0, controller=PlayerId(0)),
        build("Airbend", filter=ObjectFilter(specific=(obj.id,))),
    )
    board.refresh()
    return current(board, obj)


def colourless(board, amount):
    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(), amount)


def alternative_casts(board, obj):
    return [
        action
        for action in legal_actions(board.game, PlayerId(0))
        if action.kind is ActionKind.CAST_SPELL
        and action.source == obj.id
        and action.alternative_cost >= 0
    ]


# ---------------------------------------------------------------------------
# A resolved grant to a card in exile
# ---------------------------------------------------------------------------


def test_the_airbent_card_may_be_cast_for_two_generic_mana(board):
    """{2} of colourless pays the granted cost; Grizzly Bears' own {1}{G}
    could not be paid with it."""
    exiled = airbend(board, board.play("Grizzly Bears"))
    colourless(board, 2)
    offered = alternative_casts(board, exiled)
    assert offered

    spell = cast_spell(board.game, PlayerId(0), offered[0])
    assert spell.zone is Zone.STACK
    assert spell.alternative_cost_paid == "Airbend"
    assert board.game.player(PlayerId(0)).mana_pool.total == 0


def test_a_card_exiled_some_other_way_is_not_castable(board):
    """A control: exile alone gives no permission."""
    bears = board.play("Grizzly Bears")
    exiled = actions.exile(board.game, bears, source=0)
    board.refresh()
    colourless(board, 2)
    assert not alternative_casts(board, exiled)
    assert not board.chars(exiled).alternative_costs


def test_the_permission_ends_when_the_card_leaves_exile(board):
    """CR 400.7: in the graveyard it is a new object the grant never named."""
    exiled = airbend(board, board.play("Grizzly Bears"))
    buried = board.game.move_object(exiled, Zone.GRAVEYARD)
    board.refresh()
    colourless(board, 2)
    assert not board.chars(buried).alternative_costs
    assert not alternative_casts(board, buried)


def test_an_opponents_exiled_card_is_not_offered_to_you(board):
    """"Its owner may cast it": the exiled card is theirs, not yours."""
    exiled = airbend(board, board.play("Grizzly Bears", controller=1))
    colourless(board, 2)
    assert not alternative_casts(board, exiled)


# ---------------------------------------------------------------------------
# A static ability that speaks of another zone
# ---------------------------------------------------------------------------

GRAVEYARD_FLIERS = Ability.static(
    Effect(
        EffectKind.GRANT_ABILITY,
        targets=ObjectFilter(
            types_all=CardType.CREATURE,
            owner=ControllerRelation.YOU,
            zones=frozenset({Zone.GRAVEYARD}),
        ),
        keywords=("Flying",),
    ),
    text="creature cards in your graveyard have flying",
)


def test_a_static_ability_reaches_cards_in_a_graveyard(board):
    board.scripts.add("Glorious Anthem", GRAVEYARD_FLIERS)
    board.play("Glorious Anthem")
    mine = board.graveyard("Grizzly Bears", controller=0)
    theirs = board.graveyard("Grizzly Bears", controller=1)
    board.refresh()
    assert "Flying" in board.keywords(mine)
    assert "Flying" not in board.keywords(theirs)


def test_it_stops_when_the_static_ability_leaves(board):
    """A control: CR 611.3b, the effect lasts only while its source is on
    the battlefield."""
    board.scripts.add("Glorious Anthem", GRAVEYARD_FLIERS)
    anthem = board.play("Glorious Anthem")
    mine = board.graveyard("Grizzly Bears", controller=0)
    actions.destroy(board.game, anthem, source=0)
    board.refresh()
    assert "Flying" not in board.keywords(mine)


def test_a_battlefield_filter_does_not_reach_the_graveyard(board):
    """A control: the default zone of a filter is the battlefield, and a card
    in a graveyard is not a creature on it."""
    board.scripts.add(
        "Glorious Anthem",
        Ability.static(
            Effect(
                EffectKind.GRANT_ABILITY,
                targets=ObjectFilter(types_all=CardType.CREATURE),
                keywords=("Flying",),
            ),
        ),
    )
    board.play("Glorious Anthem")
    buried = board.graveyard("Grizzly Bears", controller=0)
    board.refresh()
    assert "Flying" not in board.keywords(buried)
