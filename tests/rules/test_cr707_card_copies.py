"""Copies of spells and of cards (CR 707.10, 707.12, 704.5e)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def give_mana(board, amount: int, color: str = "GREEN") -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(Color[color]), amount)


def bears_on_battlefield(board) -> list:
    game = board.game
    return [
        game.objects[oid]
        for oid in game.battlefield
        if game.printed_characteristics(game.objects[oid]).name == "Grizzly Bears"
    ]


def test_a_copied_permanent_spell_becomes_a_token(board):
    """CR 707.10f: as the copy resolves it stops being a copy of a spell and
    becomes a token permanent - which the state-based action for copies
    outside the stack (CR 704.5e) must then leave alone."""
    card = board.hand("Grizzly Bears")
    give_mana(board, 2)
    spell = cast_spell(board.game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    copy_spell(board.game, spell, PlayerId(0))
    board.resolve_stack()
    board.settle()

    bears = bears_on_battlefield(board)
    assert len(bears) == 2
    assert sorted(b.kind for b in bears) == [ObjectKind.CARD, ObjectKind.TOKEN]
    assert all(b.zone is Zone.BATTLEFIELD for b in bears)


def test_a_spell_that_exiles_itself_is_not_also_put_in_the_graveyard(board):
    """CR 608.2m puts an instant or sorcery in the graveyard as the last step
    of its resolution - but one that has already left the stack ("exile this
    spell") is not on the stack to be moved, and moving the old object again
    left the card in exile and in the graveyard at once."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
    from mtgfish.rules.kernel.query import ObjectFilter

    board.scripts.add(
        "Divination",
        Ability.spell(
            Effect(EffectKind.EXILE, targets=ObjectFilter(source_only=True), text="exile this spell")
        ),
    )
    card = board.hand("Divination")
    give_mana(board, 3, "BLUE")
    cast_spell(board.game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()

    game = board.game
    names = lambda ids: [game.printed_characteristics(game.objects[i]).name for i in ids]  # noqa: E731
    assert names(game.exile).count("Divination") == 1
    assert "Divination" not in names(game.player(PlayerId(0)).graveyard)
