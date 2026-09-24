"""Cascade (CR 702.85) and discover (CR 701.57)."""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import Value

P0 = PlayerId(0)


class Declines(FixedAgent):
    def choose_optional(self, game, player, effect):
        return False


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    return board


def stack_library(board, *names: str) -> list:
    """Put these cards on top of the library, first name on top."""
    game = board.game
    library = game.player(P0).library
    placed = []
    for name in reversed(names):
        obj = board.hand(name)
        game.move_object(obj, Zone.LIBRARY, to_player=P0, to_top=True)
        placed.append(name)
    return [game.printed_characteristics(game.objects[i]).name for i in library[: len(names)]]


def names(board, ids) -> list[str]:
    game = board.game
    return [game.printed_characteristics(game.objects[i]).name for i in ids]


def cascade_from(board, spell_name: str) -> None:
    spell = board.play(spell_name)  # its mana value is what matters
    execute(
        Resolution(game=board.game, source=spell.id, controller=P0),
        (Effect(EffectKind.CASCADE),),
    )


def test_cascade_exiles_down_to_a_cheaper_nonland_card_and_casts_it(board):
    """Hill Giant is 4 mana: the Forest and the 4-drop are passed over, the
    2-drop is cast, and the cascading spell itself is untouched."""
    assert stack_library(board, "Forest", "Hill Giant", "Grizzly Bears", "Runeclaw Bear")
    size = len(board.game.player(P0).library)
    cascade_from(board, "Hill Giant")

    game = board.game
    assert names(board, game.stack) == ["Grizzly Bears"]
    assert game.objects[game.stack[0]].mana_spent == 0
    library = game.player(P0).library
    assert len(library) == size - 1
    # The top is now the card that was never reached; the two passed over
    # went to the bottom.
    assert names(board, library[:1]) == ["Runeclaw Bear"]
    assert sorted(names(board, library[-2:])) == ["Forest", "Hill Giant"]
    assert not [i for i in game.exile if game.objects[i].owner == P0]


def test_cascade_leaves_other_exiled_cards_alone(board):
    """A card exiled earlier by something else is not cascade's to move."""
    game = board.game
    foretold = board.hand("Lightning Bolt")
    game.move_object(foretold, Zone.EXILE, to_player=P0)
    stack_library(board, "Grizzly Bears")
    cascade_from(board, "Hill Giant")

    assert "Lightning Bolt" in names(board, game.exile)


def test_a_declined_cascade_card_goes_to_the_bottom(board):
    board.game.agents[P0] = Declines()
    stack_library(board, "Grizzly Bears", "Runeclaw Bear")
    cascade_from(board, "Hill Giant")
    library = board.game.player(P0).library
    assert not board.game.stack
    assert names(board, library[:1]) == ["Runeclaw Bear"]
    assert names(board, library[-1:]) == ["Grizzly Bears"]


def test_discover_casts_a_card_up_to_n(board):
    stack_library(board, "Hill Giant", "Grizzly Bears")
    source = board.play("Runeclaw Bear")
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        (Effect(EffectKind.DISCOVER, amount=Value.of(2)),),
    )
    assert names(board, board.game.stack) == ["Grizzly Bears"]


def test_a_declined_discover_card_goes_to_hand(board):
    board.game.agents[P0] = Declines()
    stack_library(board, "Grizzly Bears")
    source = board.play("Runeclaw Bear")
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        (Effect(EffectKind.DISCOVER, amount=Value.of(2)),),
    )
    assert "Grizzly Bears" in names(board, board.game.player(P0).hand)


def test_casting_a_cascade_spell_cascades(board):
    """The whole path: cast, the trigger, the free spell above it."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
        KeywordInstance,
        build,
    )
    from mtgfish.rules.kernel.enums import Color

    game = board.game
    board.scripts.add("Hill Giant", *build(KeywordInstance("Cascade")))
    stack_library(board, "Grizzly Bears")
    giant = board.hand("Hill Giant")
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 4)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=giant.id))
    board.settle()  # the cascade trigger goes on the stack above the giant
    board.resolve_stack()

    creatures = names(board, game.battlefield)
    assert "Hill Giant" in creatures and "Grizzly Bears" in creatures
