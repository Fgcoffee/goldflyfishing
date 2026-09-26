"""Preparation cards and the prepared designation (CR 722)."""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr722_preparation import (
    become_prepared,
    become_unprepared,
    is_prepared,
)
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import YOU, Value

CARD = "Adventurous Eater // Have a Bite"
CREATURE = "Adventurous Eater"
SPELL = "Have a Bite"


@pytest.fixture
def board(card_db):
    if card_db.lookup(CARD) is None:
        pytest.skip("no preparation card in this card snapshot")
    board = make_board(card_db, ScriptedAbilities())
    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    for player in game.players:
        game.agents[player.id] = FixedAgent()
    # The prepare spell draws, so a resolution is visible without a target.
    board.scripts.add(
        SPELL,
        Ability.spell(
            Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card"),
        ),
    )
    return board


def enters_prepared(board) -> None:
    board.scripts.add(
        CREATURE,
        Ability.static(Effect(EffectKind.BECOME_PREPARED, text="enters prepared")),
    )


def prepare_copies(board) -> list:
    game = board.game
    return [
        game.objects[oid]
        for oid in game.exile
        if game.objects[oid].kind is ObjectKind.COPY
    ]


def copy_casts(board) -> list:
    return [
        a
        for a in legal_actions(board.game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL
        and board.game.objects[a.source].zone is Zone.EXILE
    ]


def test_becoming_prepared_makes_a_copy_of_the_prepare_spell(board):
    """CR 722.3c: a copy in exile with only the prepare spell's
    characteristics."""
    eater = board.play(CARD)
    copy = become_prepared(board.game, eater)

    assert is_prepared(board.game, eater.id)
    assert copy is not None and copy.zone is Zone.EXILE
    assert board.game.characteristics(copy).name == SPELL


def test_the_copy_survives_state_based_actions_while_prepared(board):
    """CR 722.3c is an exception to CR 704.5e."""
    eater = board.play(CARD)
    become_prepared(board.game, eater)
    board.settle()

    assert len(prepare_copies(board)) == 1


def test_the_copy_ceases_once_the_permanent_is_unprepared(board):
    eater = board.play(CARD)
    become_prepared(board.game, eater)
    become_unprepared(board.game, eater.id)
    board.settle()

    assert not prepare_copies(board)


def test_the_copy_ceases_once_the_permanent_leaves(board):
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    eater = board.play(CARD)
    become_prepared(board.game, eater)
    destroy(board.game, eater)
    board.settle()

    assert not prepare_copies(board)


def test_a_prepared_permanent_is_not_prepared_again(board):
    """CR 722.3a: not if it already has the designation."""
    eater = board.play(CARD)
    become_prepared(board.game, eater)
    assert become_prepared(board.game, eater) is None
    assert len(prepare_copies(board)) == 1


def test_only_a_permanent_with_a_prepare_spell_can_be_prepared(board):
    """CR 722.3a."""
    bears = board.play("Grizzly Bears")
    assert become_prepared(board.game, bears) is None
    assert not is_prepared(board.game, bears.id)


def test_the_card_in_hand_is_never_cast_as_its_prepare_spell(board):
    """CR 722.3 / 722.4: only its normal characteristics, in every zone."""
    board.hand(CARD)
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.BLACK), 5)
    casts = [
        a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL
    ]
    assert casts
    assert all(a.face_index == 0 for a in casts)


def test_casting_the_copy_unprepares_the_permanent(board):
    """CR 722.3c with 601.2i, and the copy is paid for like any spell."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    game = board.game
    eater = board.play(CARD)
    become_prepared(game, eater)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.BLACK), 1)

    (action,) = copy_casts(board)
    assert action.face_index == 1
    library = len(game.player(PlayerId(0)).library)
    cast_spell(game, PlayerId(0), action)
    assert not is_prepared(game, eater.id)
    board.resolve_stack()
    board.settle()

    assert len(game.player(PlayerId(0)).library) == library - 1
    assert not prepare_copies(board)
    assert not [
        oid for oid in game.player(PlayerId(0)).graveyard
        if game.characteristics(game.objects[oid]).name == SPELL
    ]


def test_the_copy_cannot_be_cast_without_the_mana(board):
    eater = board.play(CARD)
    become_prepared(board.game, eater)
    assert not copy_casts(board)


def test_it_enters_prepared(board):
    """"This creature enters prepared." (CR 722.3a) as it is cast and
    resolves, not only when an effect says so."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    enters_prepared(board)
    game = board.game
    card = board.hand(CARD)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.BLACK), 3)
    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    board.settle()

    (eater,) = [
        game.objects[oid]
        for oid in game.battlefield
        if game.characteristics(game.objects[oid]).name == CREATURE
    ]
    assert is_prepared(game, eater.id)
    assert len(prepare_copies(board)) == 1
