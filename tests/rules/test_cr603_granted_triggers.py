"""A triggered ability granted by an effect triggers like a printed one
(CR 613.1f, 603.10a)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    for name in ("Abnormal Endurance", "Shock", "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    return table


def bears(game) -> list:
    return [
        obj
        for obj in game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == "Grizzly Bears"
    ]


def cast(box, name, target):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind

    box.game.player(P0).mana_pool.add(ManaKind(Color.BLACK), 2)
    box.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    action = next(
        action
        for action in legal_actions(box.game, P0)
        if action.kind.name.startswith("CAST")
        and box.game.objects[action.source].card.name == name
    )
    assert _perform(box.game, P0, replace(action, targets=((target,),)))
    resolve_everything(box)


def resolve_everything(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def test_a_granted_dies_trigger_returns_the_creature(box):
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Abnormal Endurance", "hand", 0)
    box.put("Shock", "hand", 0)
    (creature,) = bears(box.game)
    cast(box, "Abnormal Endurance", creature.id)
    (creature,) = bears(box.game)
    cast(box, "Shock", creature.id)
    (returned,) = bears(box.game)
    assert returned.zone is Zone.BATTLEFIELD
    assert returned.id != creature.id
    assert returned.tapped


def test_without_the_grant_the_creature_stays_dead(box):
    """Control: Shock alone kills the Bears and nothing brings them back."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Shock", "hand", 0)
    (creature,) = bears(box.game)
    cast(box, "Shock", creature.id)
    (dead,) = bears(box.game)
    assert dead.zone is Zone.GRAVEYARD


def test_granted_exalted_triggers_for_each_creature_that_has_it(card_db):
    """Sublime Archangel's shape: its own exalted, and "other creatures you
    control have exalted". A lone attacker with the Archangel and two other
    creatures in play gets three triggers, not one (CR 702.90b)."""
    from harness import FixedAgent, ScriptedAbilities, make_board

    from mtgfish.rules.cr500_turn_structure.cr506_combat import declare_attackers
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
    from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
        KeywordInstance,
        build,
    )
    from mtgfish.rules.kernel.enums import CardType
    from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter

    exalted = build(KeywordInstance("Exalted"))
    others = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        other_than_source=True,
    )
    board = make_board(
        card_db,
        ScriptedAbilities(
            {
                "Serra Angel": (
                    *exalted,
                    Ability.static(
                        Effect(
                            EffectKind.GRANT_ABILITY,
                            targets=others,
                            granted_abilities=exalted,
                            text="Other creatures you control have exalted.",
                        ),
                    ),
                ),
            }
        ),
    )
    game = board.game
    game.active_player = P0
    board.play("Serra Angel", controller=0)
    attacker = board.play("Grizzly Bears", controller=0)
    board.play("Grizzly Bears", controller=0)
    game.agents[P0] = FixedAgent(attackers={attacker.id: 1})
    declare_attackers(game)
    assert len(game.pending_triggers) == 3


def _granting_board(card_db, keyword_name):
    """Mikaeus's shape: "other creatures you control have [keyword]"."""
    from harness import ScriptedAbilities, make_board

    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
    from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
        KeywordInstance,
        build,
    )
    from mtgfish.rules.kernel.enums import CardType
    from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter

    others = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        other_than_source=True,
    )
    board = make_board(
        card_db,
        ScriptedAbilities(
            {
                "Serra Angel": (
                    Ability.static(
                        Effect(
                            EffectKind.GRANT_ABILITY,
                            targets=others,
                            granted_abilities=build(KeywordInstance(keyword_name)),
                            text=f"Other creatures you control have {keyword_name.lower()}.",
                        ),
                    ),
                ),
            }
        ),
    )
    board.game.active_player = P0
    board.play("Serra Angel", controller=0)
    return board


@pytest.mark.parametrize(
    ("keyword_name", "counter"), [("Undying", "+1/+1"), ("Persist", "-1/-1")]
)
def test_a_granted_return_finds_the_card_in_the_graveyard(card_db, keyword_name, counter):
    """CR 400.7e: the granted "return it" finds the card the creature became.
    Before, the trigger resolved and the creature stayed in the graveyard."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    board = _granting_board(card_db, keyword_name)
    creature = board.play("Grizzly Bears", controller=0)
    destroy(board.game, creature)
    board.settle()
    board.resolve_stack()
    (returned,) = bears(board.game)
    assert returned.zone is Zone.BATTLEFIELD
    assert returned.counter_count(counter) == 1


def test_a_granted_return_does_not_follow_a_card_that_moved_on(card_db):
    """CR 400.7e: only while it is still where it went."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    board = _granting_board(card_db, "Undying")
    creature = board.play("Grizzly Bears", controller=0)
    destroy(board.game, creature)
    board.settle()
    (dead,) = bears(board.game)
    board.game.move_object(dead, Zone.EXILE)
    board.resolve_stack()
    (exiled,) = bears(board.game)
    assert exiled.zone is Zone.EXILE


@pytest.mark.parametrize(
    ("keyword_name", "counter"), [("Undying", "+1/+1"), ("Persist", "-1/-1")]
)
def test_a_granted_return_does_not_happen_twice(card_db, keyword_name, counter):
    """CR 702.93a, 702.79a: "if it had no [counter] on it" asks about the
    permanent as it last existed (CR 603.10a). The second time, it had one."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    board = _granting_board(card_db, keyword_name)
    creature = board.play("Grizzly Bears", controller=0)
    destroy(board.game, creature)
    board.settle()
    board.resolve_stack()
    (returned,) = bears(board.game)
    assert returned.counter_count(counter) == 1
    destroy(board.game, returned)
    board.settle()
    board.resolve_stack()
    (dead,) = bears(board.game)
    assert dead.zone is Zone.GRAVEYARD
