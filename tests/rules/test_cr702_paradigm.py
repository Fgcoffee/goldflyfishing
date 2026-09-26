"""Paradigm (CR 702.192a).

Two spell abilities: the first, only on the first resolution of a spell with
this name you control this game, delays a trigger that at the beginning of
each of your precombat main phases makes a copy of the spell in exile that
you may cast for free (CR 707.12); the second exiles the spell.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr500_turn_structure.cr500_turn import take_turn
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.cr700_additional_rules.keywords import Status, lookup
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Value

SPELL = "Divination"


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    for player in game.players:
        game.agents[player.id] = FixedAgent()
    return board


def paradigm_spell(board) -> None:
    """Divination, rewritten as a paradigm sorcery: "Draw a card. Paradigm"."""
    draw = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")
    body = build(KeywordInstance("Paradigm"))
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability

    board.scripts.add(SPELL, Ability.spell(draw, text="draw a card"), *body)


def give_blue(board, amount: int) -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(Color.BLUE), amount)


def cast_and_resolve(board) -> None:
    card = board.hand(SPELL)
    give_blue(board, 3)
    cast_spell(board.game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    board.settle()


def named(board, ids) -> list:
    game = board.game
    return [
        game.objects[i]
        for i in ids
        if game.printed_characteristics(game.objects[i]).name == SPELL
    ]


def library_size(board) -> int:
    return len(board.game.player(PlayerId(0)).library)


def test_paradigm_is_implemented():
    assert lookup("Paradigm").status is Status.IMPLEMENTED


def test_the_spell_is_exiled_rather_than_put_in_the_graveyard(board):
    paradigm_spell(board)
    cast_and_resolve(board)

    assert len(named(board, board.game.exile)) == 1
    assert not named(board, board.game.player(PlayerId(0)).graveyard)


def test_the_first_resolution_sets_up_the_repeating_trigger(board):
    paradigm_spell(board)
    cast_and_resolve(board)

    (delayed,) = board.game.delayed_triggers
    assert delayed.repeating
    assert delayed.trigger.phases == {int(Phase.PRECOMBAT_MAIN)}


def test_a_second_resolution_of_the_same_name_does_not(board):
    """CR 702.192a: "if this is the first time" - a second copy of the card
    resolving later adds no second stream of free copies."""
    paradigm_spell(board)
    cast_and_resolve(board)
    cast_and_resolve(board)

    assert len(board.game.delayed_triggers) == 1


def test_each_of_your_precombat_main_phases_casts_a_free_copy(board):
    paradigm_spell(board)
    cast_and_resolve(board)
    game = board.game

    before = library_size(board)
    game.active_player = PlayerId(0)
    take_turn(game)
    # One card for the draw step, one for the free copy's "draw a card".
    # Counted from the library: the hand is trimmed to seven at cleanup.
    assert library_size(board) == before - 2

    # The copy ceased to exist once it resolved (CR 704.5e): the card is the
    # only Divination in exile, and none reached the graveyard.
    assert len(named(board, game.exile)) == 1
    assert not named(board, game.player(PlayerId(0)).graveyard)
    # And the copy's own paradigm did not start a second stream.
    assert len(game.delayed_triggers) == 1


def test_the_copy_is_cast_so_cast_triggers_see_it(board):
    """CR 707.12: casting a copy is casting."""
    from mtgfish.rules.kernel.events import EventKind

    paradigm_spell(board)
    cast_and_resolve(board)
    game = board.game
    casts: list[int] = []

    def watch(_game, event):
        if event.kind is EventKind.CAST_SPELL:
            casts.append(event.object_id)

    game.observer = watch
    take_turn(game)
    assert len(casts) == 1
    assert game.objects[casts[0]].kind is ObjectKind.COPY
