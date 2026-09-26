"""Kindred (CR 308)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def test_a_kindred_card_has_its_creature_types(board):
    """CR 308.2: its subtypes are creature types, so "Goblin spells" and
    "Goblin cards" include a Kindred Instant - Goblin."""
    tarfire = board.hand("Tarfire")
    goblin_cards = ObjectFilter(subtypes_any=("Goblin",), zones=frozenset({Zone.HAND}))
    assert matches(board.game, tarfire, goblin_cards)


def test_a_kindred_card_is_still_its_other_type_and_not_a_creature(board):
    """CR 308.1: it is cast and resolves as its other card type; Kindred
    alone makes nothing a creature."""
    chars = board.game.characteristics(board.hand("Tarfire"))
    assert chars.has_type(CardType.INSTANT)
    assert chars.has_type(CardType.KINDRED)
    assert not chars.is_creature


def test_other_types_do_not_match(board):
    tarfire = board.hand("Tarfire")
    elves = ObjectFilter(subtypes_any=("Elf",), zones=frozenset({Zone.HAND}))
    assert not matches(board.game, tarfire, elves)
