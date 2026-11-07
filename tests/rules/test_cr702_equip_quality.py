"""CR 702.6c: "Equip [quality] [cost]" attaches only to a creature you control
with that quality; the quality narrows the usual target, it never widens it."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.kernel.enums import Supertype
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter

P0 = PlayerId(0)


def _targets_for(card_db, instance):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _candidates_for

    board = make_board(card_db, ScriptedAbilities())
    legend = board.play("Isamaru, Hound of Konda", controller=0)
    bears = board.play("Grizzly Bears", controller=0)
    their_legend = board.play("Isamaru, Hound of Konda", controller=1)
    equipment = board.play("Bonesplitter", controller=0)
    attach = build(instance)[0].effects[0]
    found = set(_candidates_for(board.game, equipment, attach, P0))
    return found, legend, bears, their_legend


def test_equip_legendary_reaches_only_your_legendary_creatures(card_db):
    found, legend, _bears, _theirs = _targets_for(
        card_db,
        KeywordInstance(
            "Equip",
            cost=Cost.mana("{3}"),
            filter=ObjectFilter(supertypes_all=Supertype.LEGENDARY),
            text="Equip legendary creature {3}",
        ),
    )
    assert found == {legend.id}


def test_plain_equip_reaches_every_creature_you_control(card_db):
    found, legend, bears, _theirs = _targets_for(
        card_db, KeywordInstance("Equip", cost=Cost.mana("{1}"), text="Equip {1}")
    )
    assert found == {legend.id, bears.id}
