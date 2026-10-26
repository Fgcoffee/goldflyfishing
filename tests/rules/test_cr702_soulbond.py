"""Soulbond and paired creatures (CR 702.95)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr702_soulbond import is_still_paired, pair, partner_of
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.matching import find
from mtgfish.rules.kernel.query import Condition, ConditionKind, ObjectFilter

P0, P1 = PlayerId(0), PlayerId(1)
PAIRED = Condition(kind=ConditionKind.IS_PAIRED)
THE_PAIR = ObjectFilter(in_pair_with_source=True)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def pair_by_effect(board, source) -> None:
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        (Effect(EffectKind.PAIR, text="pair"),),
    )


def test_the_soulbond_creature_pairs_with_another(board):
    soul = board.play("Grizzly Bears")
    other = board.play("Hill Giant")
    pair_by_effect(board, soul)
    assert partner_of(board.game, soul) is other
    assert partner_of(board.game, other) is soul
    assert holds(board.game, PAIRED, source=soul.id, controller=P0)


def test_each_of_those_creatures_means_both(board):
    soul = board.play("Grizzly Bears")
    other = board.play("Hill Giant")
    bystander = board.play("Runeclaw Bear")
    pair_by_effect(board, soul)
    found = {o.id for o in find(board.game, THE_PAIR, source=soul.id, controller=P0)}
    assert found == {soul.id, other.id}
    assert bystander.id not in found


def test_nothing_to_pair_with_pairs_nothing(board):
    soul = board.play("Grizzly Bears")
    pair_by_effect(board, soul)
    assert not is_still_paired(board.game, soul)
    assert not find(board.game, THE_PAIR, source=soul.id, controller=P0)


def test_a_creature_pairs_with_only_one(board):
    """CR 702.95d."""
    first = board.play("Grizzly Bears")
    second = board.play("Hill Giant")
    third = board.play("Runeclaw Bear")
    assert pair(board.game, first, second, P0)
    assert not pair(board.game, third, first, P0)


def test_leaving_the_battlefield_ends_the_pair(board):
    """CR 702.95e."""
    soul = board.play("Grizzly Bears")
    other = board.play("Hill Giant")
    pair(board.game, soul, other, P0)
    board.game.move_object(other, Zone.GRAVEYARD)
    assert not is_still_paired(board.game, soul)
    board.sba()
    assert soul.paired_with == NO_OBJECT


def test_another_player_gaining_control_ends_the_pair(board):
    """CR 702.95e, and it stays ended if control comes back."""
    soul = board.play("Grizzly Bears")
    other = board.play("Hill Giant")
    pair(board.game, soul, other, P0)
    other.controller = other.base_controller = P1
    board.refresh()
    board.sba()
    other.controller = other.base_controller = P0
    board.refresh()
    assert not is_still_paired(board.game, soul)


def test_the_pair_cannot_form_across_controllers(board):
    """CR 702.95c: both under the soulbond ability's controller."""
    soul = board.play("Grizzly Bears")
    theirs = board.play("Hill Giant", controller=1)
    assert not pair(board.game, soul, theirs, P0)
