"""The record of how a spell was paid for: colours spent (CR 702.44a,
sunburst and converge) and Phyrexian symbols paid with life (CR 702.150a,
compleated)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import Value, ValueKind
from mtgfish.rules.kernel.values import evaluate

P0 = PlayerId(0)
COLORS_SPENT = Value(kind=ValueKind.COLORS_OF_MANA_SPENT)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def cast(board, name: str, *mana: tuple[Color, int]):
    pool = board.game.player(P0).mana_pool
    for color, amount in mana:
        pool.add(ManaKind(color), amount)
    card = board.hand(name)
    return cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))


def battlefield(board, name: str):
    game = board.game
    (obj,) = [
        game.objects[i] for i in game.battlefield
        if game.printed_characteristics(game.objects[i]).name == name
    ]
    return obj


def test_two_colours_spent_count_as_two(board):
    """Grizzly Bears costs {1}{G}: paid with green and red, two colours."""
    spell = cast(board, "Grizzly Bears", (Color.GREEN, 1), (Color.RED, 1))
    assert evaluate(board.game, COLORS_SPENT, source=spell.id, controller=P0) == 2


def test_colourless_mana_is_no_colour(board):
    spell = cast(board, "Grizzly Bears", (Color.GREEN, 1), (Color.NONE, 1))
    assert evaluate(board.game, COLORS_SPENT, source=spell.id, controller=P0) == 1


def test_the_permanent_remembers_what_was_spent_on_the_spell(board):
    """Sunburst counts as it enters, so the record crosses to the permanent."""
    cast(board, "Grizzly Bears", (Color.GREEN, 1), (Color.BLUE, 1))
    board.resolve_stack()
    bears = battlefield(board, "Grizzly Bears")
    assert evaluate(board.game, COLORS_SPENT, source=bears.id, controller=P0) == 2


def test_compleated_enters_with_two_fewer_loyalty_per_life_paid_symbol(card_db):
    """CR 702.150a. Tamiyo, Compleated Sage costs {2}{G}{G/U/P}{U} and has 5
    loyalty; paying the Phyrexian symbol with 2 life leaves 3."""
    board = make_board(
        card_db, ScriptedAbilities({"Tamiyo, Compleated Sage": (keyword("Compleated"),)})
    )
    if card_db.lookup("Tamiyo, Compleated Sage") is None:
        pytest.skip("card not in this snapshot")
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    # Four mana for five symbols: the Phyrexian one is paid with 2 life.
    cast(board, "Tamiyo, Compleated Sage", (Color.GREEN, 1), (Color.BLUE, 1), (Color.NONE, 2))
    spell = game.objects[game.stack[-1]]
    assert game.player(P0).life == 38
    assert spell.phyrexian_paid_with_life == 1
    board.resolve_stack()
    tamiyo = battlefield(board, "Tamiyo, Compleated Sage")
    assert tamiyo.counter_count("loyalty") == 3
    assert tamiyo.zone is Zone.BATTLEFIELD


def test_without_compleated_life_paid_changes_nothing(card_db):
    """Control: the reduction belongs to the keyword, not to paying life."""
    board = make_board(card_db, ScriptedAbilities())
    if card_db.lookup("Tamiyo, Compleated Sage") is None:
        pytest.skip("card not in this snapshot")
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    cast(board, "Tamiyo, Compleated Sage", (Color.GREEN, 1), (Color.BLUE, 1), (Color.NONE, 2))
    board.resolve_stack()
    assert battlefield(board, "Tamiyo, Compleated Sage").counter_count("loyalty") == 5
