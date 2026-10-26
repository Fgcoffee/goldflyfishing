"""An ability granted to "spells you cast" is there when the spell's costs
are chosen (CR 601.2a-b): Wort, the Raidmother's granted conspire."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter, Value

P0 = PlayerId(0)

RED_INSTANTS_YOU_CAST = ObjectFilter(
    types_any=CardType.INSTANT | CardType.SORCERY,
    colors_any=Color.RED,
    controller=ControllerRelation.YOU,
    zones=frozenset({Zone.STACK}),
)


def board_with(card_db, grant: bool):
    scripts = {
        "Lightning Bolt": (
            Ability.spell(Effect(EffectKind.GAIN_LIFE, amount=Value.of(3), text="gain 3")),
        ),
    }
    if grant:
        scripts["Serra Angel"] = (
            Ability.static(
                Effect(
                    EffectKind.GRANT_ABILITY,
                    targets=RED_INSTANTS_YOU_CAST,
                    granted_abilities=build(KeywordInstance("Conspire")),
                    text="Each red instant or sorcery spell you cast has conspire.",
                ),
            ),
        )
    board = make_board(card_db, ScriptedAbilities(scripts))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    board.play("Serra Angel")
    board.play("Goblin Piker")
    board.play("Goblin Piker")
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    return board


def casts(board, card):
    return [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


@pytest.fixture
def need_cards(card_db):
    for name in ("Goblin Piker", "Serra Angel", "Lightning Bolt"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def test_a_granted_conspire_is_offered(card_db, need_cards):
    board = board_with(card_db, grant=True)
    bolt = board.hand("Lightning Bolt")
    offered = sorted(a.additional_costs for a in casts(board, bolt))
    assert offered == [(), (0,)]


def test_paying_the_granted_conspire_taps_two_red_creatures(card_db, need_cards):
    board = board_with(card_db, grant=True)
    bolt = board.hand("Lightning Bolt")
    (action,) = [a for a in casts(board, bolt) if a.additional_costs]
    cast_spell(board.game, P0, action)
    pikers = [
        o for o in board.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == "Goblin Piker"
    ]
    assert all(p.tapped for p in pikers)


def test_without_the_grant_no_conspire(card_db, need_cards):
    board = board_with(card_db, grant=False)
    bolt = board.hand("Lightning Bolt")
    assert sorted(a.additional_costs for a in casts(board, bolt)) == [()]
