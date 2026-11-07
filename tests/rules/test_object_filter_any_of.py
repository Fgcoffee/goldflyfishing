"""A filter can say "one of these" across fields ("creature or Vehicle",
"instant, sorcery, or Wizard spell"): matching, counting and targeting all
honour it."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import find, matches
from mtgfish.rules.kernel.query import (
    ControllerRelation,
    ObjectFilter,
    Value,
    ValueKind,
)
from mtgfish.rules.kernel.values import evaluate

P0 = PlayerId(0)

CREATURE_OR_VEHICLE = ObjectFilter(
    any_of=(
        ObjectFilter(types_all=CardType.CREATURE),
        ObjectFilter(subtypes_any=("Vehicle",)),
    ),
    controller=ControllerRelation.YOU,
)


def board_with(card_db):
    board = make_board(card_db, ScriptedAbilities())
    bears = board.play("Grizzly Bears", controller=0)
    vehicle = board.play("Smuggler's Copter", controller=0)
    forest = board.play("Forest", controller=0)
    theirs = board.play("Hill Giant", controller=1)
    return board, bears, vehicle, forest, theirs


def test_either_alternative_matches_and_the_other_fields_still_apply(card_db):
    board, bears, vehicle, forest, theirs = board_with(card_db)
    game = board.game
    assert matches(game, bears, CREATURE_OR_VEHICLE, controller=P0)
    assert matches(game, vehicle, CREATURE_OR_VEHICLE, controller=P0)
    assert not matches(game, forest, CREATURE_OR_VEHICLE, controller=P0)
    assert not matches(game, theirs, CREATURE_OR_VEHICLE, controller=P0), "you control"


def test_counting_with_a_disjunction(card_db):
    board, *_ = board_with(card_db)
    assert evaluate(
        board.game, Value(ValueKind.COUNT, filter=CREATURE_OR_VEHICLE), controller=P0
    ) == 2
    assert {o.card.name for o in find(board.game, CREATURE_OR_VEHICLE, controller=P0)} == {
        "Grizzly Bears", "Smuggler's Copter"}


def test_targeting_with_a_disjunction(card_db):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _candidates_for

    board, bears, vehicle, forest, theirs = board_with(card_db)
    effect = Effect(EffectKind.DESTROY, targets=CREATURE_OR_VEHICLE, is_targeted=True)
    source = board.hand("Shock")
    candidates = set(_candidates_for(board.game, source, effect, P0))
    assert candidates == {bears.id, vehicle.id}


def test_the_explainer_says_it(card_db):
    assert "or" in CREATURE_OR_VEHICLE.describe()
