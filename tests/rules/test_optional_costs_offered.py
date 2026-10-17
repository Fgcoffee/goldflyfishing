"""Optional additional costs are offered as a way to cast the spell when they
can be paid (CR 601.2b, 118.8a)."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import (
    AdditionalCost,
    Cost,
    CostComponent,
    CostKind,
)
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)

BUYBACK = Ability(
    AbilityKind.STATIC,
    keyword="Buyback",
    additional_cost=AdditionalCost(
        cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{3}")),)),
        optional=True,
        keyword="Buyback",
        text="Buyback {3}",
    ),
    functions_in=frozenset({Zone.HAND, Zone.STACK}),
    text="Buyback {3}",
)


def setup(card_db, mana):
    board = make_board(card_db, ScriptedAbilities({"Divination": (BUYBACK,)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), mana)
    return board


def casts(board, card):
    return [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def test_buyback_is_offered_when_it_can_be_paid(card_db):
    board = setup(card_db, 6)
    card = board.hand("Divination")
    assert sorted(a.additional_costs for a in casts(board, card)) == [(), (0,)]


def test_buyback_is_not_offered_when_only_the_spell_can_be_paid(card_db):
    board = setup(card_db, 5)
    card = board.hand("Divination")
    assert [a.additional_costs for a in casts(board, card)] == [()]


def test_the_offered_action_pays_the_buyback(card_db):
    board = setup(card_db, 6)
    card = board.hand("Divination")
    (action,) = [a for a in casts(board, card) if a.additional_costs]
    cast_spell(board.game, P0, action)
    assert board.game.player(P0).mana_pool.total == 0
