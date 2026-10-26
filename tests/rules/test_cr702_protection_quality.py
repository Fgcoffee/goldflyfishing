"""Protection is from a stated quality (CR 702.16a).

A protection keyword whose quality was never read states no quality, so it
stops nothing. It used to be read as protection from everything: a creature
with an unread "protection from ..." could not be targeted or damaged by
anything at all.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.actions import deal_damage
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _has_protection_from
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def bolt_at_bears(board, protection: KeywordInstance):
    board.scripts.add("Grizzly Bears", *build(protection))
    bears = board.play("Grizzly Bears", controller=0)
    bolt = board.hand("Lightning Bolt", controller=1)
    return bears, bolt


def test_protection_without_a_quality_stops_nothing(board):
    bears, bolt = bolt_at_bears(board, KeywordInstance("Protection"))

    assert not _has_protection_from(board.game, bears, bolt)
    dealt = deal_damage(board.game, bears, 3, source=bolt.id, source_controller=1)
    assert dealt == 3
    assert bears.damage == 3


def test_protection_from_red_still_stops_a_red_source(board):
    """Control: a quality that was read still protects."""
    red = ObjectFilter(colors_any=Color.RED, zones=frozenset())
    bears, bolt = bolt_at_bears(board, KeywordInstance("Protection", filter=red))

    assert _has_protection_from(board.game, bears, bolt)
    dealt = deal_damage(board.game, bears, 3, source=bolt.id, source_controller=1)
    assert dealt == 0
    assert bears.damage == 0


def test_protection_from_everything_is_a_filter_every_source_matches(board):
    """CR 702.16j: protection from everything is written as a quality with no
    constraints and no zone, which every source matches."""
    everything = ObjectFilter(zones=frozenset())
    bears, bolt = bolt_at_bears(board, KeywordInstance("Protection", filter=everything))

    assert _has_protection_from(board.game, bears, bolt)
    assert deal_damage(board.game, bears, 3, source=bolt.id, source_controller=1) == 0
