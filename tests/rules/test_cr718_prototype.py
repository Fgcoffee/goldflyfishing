"""Prototype cards (CR 718, 702.160).

    Goring Warplow - Artifact Creature - Construct {6}, 5/4
    Prototype {1}{B} - 1/1
    Deathtouch

Casting it prototyped is a choice of characteristics, not an alternative cost
(CR 718.3): the spell *is* a black {1}{B} 1/1 on the stack, and the permanent
it becomes stays one (CR 718.3b). Everywhere else the card is its normal
colourless {6} 5/4 (CR 718.4), and nothing but those three values and colour
change (CR 718.5).

Before this, "Prototype" was offered as an alternative cost that charged
{1}{B} and left every characteristic alone - a 5/4 deathtoucher for two mana.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr300_card_types.cr300_card_types import CastMode
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_permanent, copy_spell
from mtgfish.rules.kernel.enums import LETTER_TO_COLOR, Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

WARPLOW = "Goring Warplow"
YOU = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.scripts.add(WARPLOW, keyword("Deathtouch"))
    board.game.active_player = YOU
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _mana(board, black: int = 0, generic: int = 0) -> None:
    pool = board.game.player(YOU).mana_pool
    if black:
        pool.add(ManaKind(LETTER_TO_COLOR["B"]), black)
    if generic:
        pool.add(ManaKind(), generic)


def _cast(board, face_index: int):
    card = board.hand(WARPLOW)
    return cast_spell(
        board.game, YOU, Action(ActionKind.CAST_SPELL, source=card.id, face_index=face_index)
    )


def _resolved(board, spell):
    board.resolve_stack()
    return board.game.objects[spell.superseded_by]


def _casts(board, card):
    return [
        a for a in legal_actions(board.game, YOU)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


# ---------------------------------------------------------------------------
# CR 718.3, 718.3a: the choice, and what it is judged on
# ---------------------------------------------------------------------------


def test_two_mana_offers_only_the_prototyped_cast(board):
    """CR 718.3a: castability is judged on the prototype cost."""
    card = board.hand(WARPLOW)
    _mana(board, black=1, generic=1)
    assert [a.face_index for a in _casts(board, card)] == [1]


def test_six_mana_offers_both(board):
    card = board.hand(WARPLOW)
    _mana(board, black=1, generic=5)
    assert sorted(a.face_index for a in _casts(board, card)) == [0, 1]


def test_prototype_is_not_an_alternative_cost(board):
    """CR 718.3: it is a set of characteristics, so the alternative-cost list
    (CR 118.9) does not offer it a second time."""
    card = board.hand(WARPLOW)
    alternatives = board.game.characteristics(card).alternative_costs
    assert not [a for a in alternatives if a.keyword == "Prototype"]


# ---------------------------------------------------------------------------
# CR 718.3b: on the stack and on the battlefield
# ---------------------------------------------------------------------------


def test_the_prototyped_spell_has_the_prototype_characteristics(board):
    _mana(board, black=1, generic=1)
    spell = _cast(board, 1)
    chars = board.chars(spell)
    assert spell.cast_mode == int(CastMode.PROTOTYPE)
    assert str(chars.mana_cost) == "{1}{B}"
    assert chars.mana_value == 2
    assert (chars.power, chars.toughness) == (1, 1)
    assert chars.colors == Color.BLACK
    # The two-mana payment was the whole cost.
    assert board.game.player(YOU).mana_pool.total == 0


def test_the_permanent_keeps_them(board):
    _mana(board, black=1, generic=1)
    permanent = _resolved(board, _cast(board, 1))
    assert permanent.zone is Zone.BATTLEFIELD
    assert board.pt(permanent) == (1, 1)
    chars = board.chars(permanent)
    assert chars.mana_value == 2
    assert chars.colors == Color.BLACK
    assert permanent.cast_mode == int(CastMode.PROTOTYPE)


def test_it_keeps_its_name_types_and_abilities(board):
    """CR 718.5."""
    _mana(board, black=1, generic=1)
    permanent = _resolved(board, _cast(board, 1))
    chars = board.chars(permanent)
    assert chars.name == WARPLOW
    assert "Construct" in chars.type_line.subtypes
    assert "Deathtouch" in board.keywords(permanent)


def test_cast_normally_it_is_the_full_sized_card(board):
    """The control: the normal cast is untouched."""
    _mana(board, black=1, generic=5)
    permanent = _resolved(board, _cast(board, 0))
    chars = board.chars(permanent)
    assert board.pt(permanent) == (5, 4)
    assert chars.mana_value == 6
    assert chars.colors == Color.NONE


# ---------------------------------------------------------------------------
# CR 718.4: every other zone
# ---------------------------------------------------------------------------


def test_off_the_battlefield_it_is_normal_again(board):
    _mana(board, black=1, generic=1)
    permanent = _resolved(board, _cast(board, 1))
    in_hand = board.game.move_object(permanent, Zone.HAND)
    chars = board.chars(in_hand)
    assert (chars.power, chars.toughness) == (5, 4)
    assert chars.mana_value == 6
    assert in_hand.cast_mode == int(CastMode.NORMAL)


def test_put_onto_the_battlefield_it_is_normal(board):
    """CR 718.4: only a prototyped *cast* gives the smaller body."""
    card = board.graveyard(WARPLOW)
    permanent = board.game.move_object(card, Zone.BATTLEFIELD)
    assert board.pt(permanent) == (5, 4)


# ---------------------------------------------------------------------------
# CR 718.2a, 718.3c, 718.3d: copies
# ---------------------------------------------------------------------------


def test_a_copy_of_a_prototyped_spell_is_prototyped(board):
    """CR 718.3c."""
    _mana(board, black=1, generic=1)
    spell = _cast(board, 1)
    copy = copy_spell(board.game, spell)
    assert copy.cast_mode == int(CastMode.PROTOTYPE)
    chars = board.chars(copy)
    assert (chars.power, chars.toughness) == (1, 1)
    assert chars.mana_value == 2


def test_a_copy_of_a_prototyped_permanent_is_prototyped(board):
    """CR 718.3d: the alternative values are copiable (CR 718.2a)."""
    _mana(board, black=1, generic=1)
    original = _resolved(board, _cast(board, 1))
    clone = board.play("Grizzly Bears")
    copy_permanent(board.game, clone, original)
    board.refresh()
    assert board.pt(clone) == (1, 1)
    assert board.chars(clone).mana_value == 2
    assert board.chars(clone).name == WARPLOW
