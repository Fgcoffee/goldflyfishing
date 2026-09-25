"""Adventures and Omens beyond casting and resolving (CR 715, 720).

- CR 715.2a / 720.2a: "has an Adventure" is a question about the card, true
  whether or not the object is using the Adventure's characteristics. Edgewall
  Innkeeper and its kin ask it of creature spells, which never are.
- CR 715.3c / 720.3c: a copy of an Adventure or Omen spell is one too, so a
  rule asking how the spell was cast gets the same answer from the copy.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.compile import parse_card
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr300_card_types.cr300_card_types import CastMode
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import LETTER_TO_COLOR, CardType, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import ObjectFilter

ADVENTURE = "Gollum, Silent Slinker"   # Creature // Sorcery - Adventure
OMEN = "Marang River Regent"           # Creature // Instant - Omen
YOU = PlayerId(0)

HAS_ADVENTURE = ObjectFilter(has_inset="Adventure", zones=frozenset())
HAS_OMEN = ObjectFilter(has_inset="Omen", zones=frozenset())


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = YOU
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    pool = board.game.player(YOU).mana_pool
    for letter in "WUBRG":
        pool.add(ManaKind(LETTER_TO_COLOR[letter]), 8)
    return board


def _matches(board, obj, spec):
    return matches(board.game, obj, spec, source=0, controller=YOU)


def _cast(board, name, face_index):
    card = board.hand(name)
    return cast_spell(
        board.game, YOU, Action(ActionKind.CAST_SPELL, source=card.id, face_index=face_index)
    )


# ---------------------------------------------------------------------------
# CR 715.2a, 720.2a
# ---------------------------------------------------------------------------


def test_an_adventurer_card_has_an_adventure_everywhere(board):
    assert _matches(board, board.hand(ADVENTURE), HAS_ADVENTURE)
    assert _matches(board, board.play(ADVENTURE), HAS_ADVENTURE)


def test_the_creature_spell_has_an_adventure_too(board):
    """What Edgewall Innkeeper asks: the creature half, cast as a creature."""
    spell = _cast(board, ADVENTURE, 0)
    assert board.chars(spell).type_line.has_type(CardType.CREATURE)
    assert _matches(board, spell, HAS_ADVENTURE)


def test_an_ordinary_card_has_no_adventure(board):
    assert not _matches(board, board.play("Grizzly Bears"), HAS_ADVENTURE)


def test_an_omen_is_not_an_adventure(board):
    omen = board.play(OMEN)
    assert _matches(board, omen, HAS_OMEN)
    assert not _matches(board, omen, HAS_ADVENTURE)


def test_a_face_down_adventurer_has_no_adventure(board):
    """CR 708.2: the face-down values list none."""
    assert not _matches(board, board.play(ADVENTURE, face_down=True), HAS_ADVENTURE)


def test_the_parser_reads_that_has_an_adventure(card_db):
    parsed = parse_card(card_db.lookup("Edgewall Innkeeper"))
    (ability,) = [a for face in parsed.faces for a in face.abilities if a.trigger]
    assert not ability.unparsed
    assert ability.trigger.subject.has_inset == "Adventure"


# ---------------------------------------------------------------------------
# CR 715.3c, 720.3c
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "mode"), [(ADVENTURE, CastMode.ADVENTURE), (OMEN, CastMode.OMEN)])
def test_a_copy_of_the_inset_spell_is_one_too(board, name, mode):
    spell = _cast(board, name, 1)
    copy = copy_spell(board.game, spell)
    assert copy.cast_mode == int(mode)
    assert board.chars(copy).name == board.chars(spell).name
    assert mode.name.title() in board.chars(copy).type_line.subtypes


def test_a_copy_of_an_adventurer_has_an_adventure(board):
    """CR 715.2b: the Adventure is part of the copiable values."""
    from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_permanent

    original = board.play(ADVENTURE)
    clone = board.play("Grizzly Bears")
    assert not _matches(board, clone, HAS_ADVENTURE)
    copy_permanent(board.game, clone, original)
    board.refresh()
    assert _matches(board, clone, HAS_ADVENTURE)


def test_a_copy_of_an_ordinary_card_loses_the_adventure(board):
    """The other direction: an adventurer copying Grizzly Bears has none."""
    from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_permanent

    adventurer = board.play(ADVENTURE)
    copy_permanent(board.game, adventurer, board.play("Grizzly Bears"))
    board.refresh()
    assert not _matches(board, adventurer, HAS_ADVENTURE)
