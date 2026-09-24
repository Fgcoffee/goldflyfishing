"""Printed characteristics (CR 204, 208, 209, 210).

What ``from_face`` makes of a printed card face, before any continuous effect
has touched it: which colors a color indicator gives an object, which of two
printed numbers is power, and what a ``*`` means.

These are the values CR 613.2 calls the copiable base, so every later question
about the object starts from them.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.data.cards import FaceDef
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr200_parts_of_a_card.characteristics import from_face
from mtgfish.rules.cr200_parts_of_a_card.cr205_typeline import TypeLine
from mtgfish.rules.kernel.enums import CardType, Color


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _face(**changes) -> FaceDef:
    """A minimal printed face, with whatever the test cares about changed."""
    defaults = {
        "name": "Test Face",
        "mana_cost": ManaCost.parse("{1}{R}"),
        "has_mana_cost": True,
        "type_line": TypeLine(types=CardType.CREATURE),
        "oracle_text": "",
        "colors": Color.RED,
    }
    defaults.update(changes)
    return FaceDef(**defaults)


# ---------------------------------------------------------------------------
# CR 204.2: a color indicator
# ---------------------------------------------------------------------------


def test_a_color_indicator_gives_the_object_its_colors():
    """CR 204.2: the object is each color the indicator denotes."""
    face = _face(color_indicator=Color.BLUE)
    assert from_face(face).colors is Color.BLUE


def test_a_color_indicator_replaces_the_cost_derived_colors():
    """CR 204.2 with CR 202.2: the indicator is the answer, not an addition to
    it. The back face of a transforming card has no mana cost to be a color
    from in the first place."""
    face = _face(
        mana_cost=ManaCost.parse(""),
        has_mana_cost=False,
        colors=Color.RED,
        color_indicator=Color.GREEN | Color.WHITE,
    )
    colors = from_face(face).colors
    assert colors == Color.GREEN | Color.WHITE
    assert not colors & Color.RED


def test_without_an_indicator_the_mana_cost_decides():
    """CR 202.2, the ordinary case."""
    assert from_face(_face()).colors is Color.RED


def test_a_colorless_indicator_is_not_the_same_as_no_indicator():
    """CR 204.2: an indicator denoting no colors makes the object colorless,
    where no indicator at all leaves the mana cost in charge. ``None`` and
    ``Color.NONE`` are therefore not interchangeable."""
    assert from_face(_face(color_indicator=Color.NONE)).colors is Color.NONE
    assert from_face(_face(color_indicator=None)).colors is Color.RED


# ---------------------------------------------------------------------------
# CR 208: power and toughness
# ---------------------------------------------------------------------------


def test_power_is_the_first_number_and_toughness_the_second(board):
    """CR 208.1, on a card where the two differ."""
    card = board.db.lookup("Thing in the Ice")
    chars = from_face(card.faces[0])
    assert (chars.power, chars.toughness) == (0, 4)


def test_a_star_is_not_a_zero(board):
    """CR 208.2 and CR 208.2a: a printed ``*`` means a characteristic-defining
    ability decides, so the printed value is no value at all. Read as zero it
    would make Tarmogoyf a 0/1 and the ability would never be asked."""
    card = board.db.lookup("Tarmogoyf")
    chars = from_face(card.faces[0])
    assert chars.power is None
    assert chars.toughness is None


def test_a_creature_with_no_value_for_its_power_has_zero(board):
    """CR 208.5. A ``*`` with no characteristic-defining ability to set it -
    here because nothing granted Tarmogoyf its abilities - is 0/0 on the
    battlefield, and the 0 toughness is lethal (CR 704.5f).

    The filling-in happens in the layer system rather than here; nothing in
    this package writes a characteristic.
    """
    goyf = board.play("Tarmogoyf", controller=0)
    assert board.pt(goyf) == (0, 0)
    assert board.sba()
    assert board.alive(0) == []


def test_a_noncreature_card_has_no_power_or_toughness(board):
    """CR 208.3: and None, not 0, is how that is said."""
    card = board.db.lookup("Sol Ring")
    chars = from_face(card.faces[0])
    assert chars.power is None
    assert chars.toughness is None


# ---------------------------------------------------------------------------
# CR 209.1, CR 210.1: loyalty and defense
# ---------------------------------------------------------------------------


def test_a_planeswalker_card_has_its_printed_loyalty(board):
    """CR 209.1."""
    card = board.db.lookup("Chandra, Torch of Defiance")
    assert from_face(card.faces[0]).loyalty == 4


def test_a_battle_card_has_its_printed_defense(board):
    """CR 210.1."""
    card = board.db.lookup("Invasion of Alara")
    assert from_face(card.faces[0]).defense == 7


def test_a_creature_card_has_neither(board):
    card = board.db.lookup("Grizzly Bears")
    chars = from_face(card.faces[0])
    assert chars.loyalty is None
    assert chars.defense is None
