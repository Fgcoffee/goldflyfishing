"""Where a spell with alternative characteristics goes (CR 715.3d, 720.3d).

An Adventure and an Omen are both a creature card with a spell printed on the
other half, and both are cast by choosing that half. What separates them is
what happens when the spell resolves: CR 715.3d exiles the Adventure so its
controller may cast the creature later, and CR 720.3d shuffles the Omen into
its owner's library. Neither goes to the graveyard.

The engine had the tables saying so and never consulted them, so both went to
the graveyard - which for an Adventure means the creature half is simply lost.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr300_card_types.cr300_card_types import (
    CastMode,
    cast_mode_for_face,
    resolution_zone,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId

ADVENTURE = "Gollum, Silent Slinker"   # Creature // Sorcery - Adventure
OMEN = "Marang River Regent"           # Creature // Instant - Omen
PLAIN = "Lightning Bolt"


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    _give_mana(board)
    return board


def _give_mana(board, player: int = 0, each: int = 8) -> None:
    """Enough of every colour that no test fails for a reason it is not about."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import LETTER_TO_COLOR

    pool = board.game.player(PlayerId(player)).mana_pool
    for letter in "WUBRG":
        pool.add(ManaKind(LETTER_TO_COLOR[letter]), each)


def _cast_face(board, name, face_index):
    card = board.hand(name, controller=0)
    spell = cast_spell(
        board.game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, face_index=face_index),
    )
    return spell


def _final_zone(board, spell):
    resolve_top(board.game)
    live = board.game.objects.get(spell.superseded_by or spell.id)
    while live is not None and live.superseded_by:
        live = board.game.objects.get(live.superseded_by)
    return live.zone if live else None


# ---------------------------------------------------------------------------
# Reading the mode off the face
# ---------------------------------------------------------------------------


def test_the_front_face_is_the_ordinary_mode(board):
    card = board.hand(ADVENTURE, controller=0)
    assert cast_mode_for_face(card, 0) is CastMode.NORMAL


def test_an_adventure_half_is_the_adventure_mode(board):
    card = board.hand(ADVENTURE, controller=0)
    assert cast_mode_for_face(card, 1) is CastMode.ADVENTURE


def test_an_omen_half_is_the_omen_mode(board):
    """The case a layout test gets wrong: the card data gives an Omen the same
    layout as an Adventure, so only the subtype tells them apart."""
    card = board.hand(OMEN, controller=0)
    assert cast_mode_for_face(card, 1) is CastMode.OMEN


def test_a_card_with_one_face_has_no_alternative_mode(board):
    card = board.hand(PLAIN, controller=0)
    assert cast_mode_for_face(card, 1) is CastMode.NORMAL


# ---------------------------------------------------------------------------
# What each mode does on resolution
# ---------------------------------------------------------------------------


def test_the_adventure_mode_exiles_on_resolution(board):
    """CR 715.3d."""
    assert resolution_zone(CastMode.ADVENTURE) is Zone.EXILE


def test_the_omen_mode_shuffles_into_the_library(board):
    """CR 720.3d."""
    assert resolution_zone(CastMode.OMEN) is Zone.LIBRARY


def test_the_ordinary_mode_names_no_zone(board):
    """CR 608.2m still applies to everything else."""
    assert resolution_zone(CastMode.NORMAL) is None


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_casting_an_adventure_records_the_mode(board):
    spell = _cast_face(board, ADVENTURE, 1)
    assert spell.cast_mode == int(CastMode.ADVENTURE)


def test_a_resolved_adventure_is_exiled_not_buried(board):
    """CR 715.3d, the point of the whole rule: the creature half is still
    there to be cast later, and a graveyard would have lost it."""
    spell = _cast_face(board, ADVENTURE, 1)
    assert _final_zone(board, spell) is Zone.EXILE


def test_a_resolved_omen_goes_to_the_library(board):
    """CR 720.3d."""
    spell = _cast_face(board, OMEN, 1)
    assert _final_zone(board, spell) is Zone.LIBRARY


def test_an_ordinary_spell_still_goes_to_the_graveyard(board):
    """The control. CR 608.2m is unchanged for everything without alternative
    characteristics."""
    spell = _cast_face(board, PLAIN, 0)
    assert _final_zone(board, spell) is Zone.GRAVEYARD


def test_casting_the_creature_half_is_an_ordinary_cast(board):
    """The other control: choosing the front face is not choosing a mode, so
    nothing about resolution changes."""
    card = board.hand(ADVENTURE, controller=0)
    spell = cast_spell(
        board.game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, face_index=0),
    )
    assert spell.cast_mode == int(CastMode.NORMAL)


# ---------------------------------------------------------------------------
# CR 715.3d's other half: the exiled card may be played
# ---------------------------------------------------------------------------


def _castable_faces(board, object_id):
    from mtgfish.rules.kernel.legality import legal_actions

    return sorted(
        action.face_index
        for action in legal_actions(board.game, PlayerId(0))
        if action.kind is ActionKind.CAST_SPELL and action.source == object_id
    )


def test_the_exiled_card_may_be_played_by_its_controller(board):
    """CR 715.3d: "for as long as that card remains exiled, that player may
    play it". Exiling it and stopping there would lose the creature entirely,
    which is worse than the graveyard it used to go to."""
    spell = _cast_face(board, ADVENTURE, 1)
    resolve_top(board.game)
    exiled = board.game.objects[spell.superseded_by]

    assert exiled.playable_from_here_by == PlayerId(0)
    assert _castable_faces(board, exiled.id) == [0]


def test_it_cannot_be_cast_as_the_adventure_again(board):
    """CR 715.3d: "It can't be cast as an Adventure this way" - otherwise the
    Adventure half loops for ever."""
    spell = _cast_face(board, ADVENTURE, 1)
    resolve_top(board.game)
    exiled = board.game.objects[spell.superseded_by]

    assert 1 not in _castable_faces(board, exiled.id)


def test_an_opponent_may_not_play_it(board):
    """The permission names a player."""
    from mtgfish.rules.kernel.legality import legal_actions

    spell = _cast_face(board, ADVENTURE, 1)
    resolve_top(board.game)
    exiled = board.game.objects[spell.superseded_by]

    assert not [
        a
        for a in legal_actions(board.game, PlayerId(1))
        if a.kind is ActionKind.CAST_SPELL and a.source == exiled.id
    ]


def test_an_ordinary_exiled_card_carries_no_permission(board):
    """The control: exile is not by itself permission to play anything."""
    from mtgfish.rules.cr100_game_concepts.actions import exile

    card = board.hand(PLAIN, controller=0)
    gone = exile(board.game, card)

    assert gone.playable_from_here_by == -1 or not _castable_faces(board, gone.id)
