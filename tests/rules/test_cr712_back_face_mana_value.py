"""A transforming double-faced card with its back face up has its front
face's mana value; a copy of that back face has 0 (CR 712.8c, 712.8e)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.kernel.enums import Layout

FRONT_AND_BACK = "Delver of Secrets // Insectile Aberration"


@pytest.fixture
def board(card_db):
    card = card_db.lookup(FRONT_AND_BACK) or card_db.lookup("Delver of Secrets")
    if card is None or getattr(card, "layout", None) is not Layout.TRANSFORM:
        pytest.skip("no transforming card in this card pool")
    return make_board(card_db, ScriptedAbilities())


def delver(board, face_index):
    obj = board.play("Delver of Secrets")
    obj.face_index = face_index
    board.game.invalidate_characteristics()
    return obj


def test_the_front_face_has_its_own_mana_value(board):
    assert board.chars(delver(board, 0)).mana_value == 1


def test_the_back_face_up_has_the_front_faces_mana_value(board):
    """Insectile Aberration has no mana cost, and still mana value 1."""
    back = board.chars(delver(board, 1))
    assert back.name == "Insectile Aberration"
    assert back.mana_value == 1


def test_a_copy_of_the_back_face_has_mana_value_zero(board):
    from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_permanent

    original = delver(board, 1)
    copier = board.play("Grizzly Bears")
    assert copy_permanent(board.game, copier, original) is not False
    board.game.invalidate_characteristics()
    copied = board.chars(copier)
    assert copied.name == "Insectile Aberration"
    assert copied.mana_value == 0
