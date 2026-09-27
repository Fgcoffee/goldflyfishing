"""Looking at / revealing the top of a library and dealing out the pile.

CR 701.16a: revealing moves nothing. The looked-at cards are a pile that the
following instructions choose from; what is put back into the library is
repositioned, not moved to a new zone (CR 400.7 needs a zone change); "the
bottom" is the bottom; and "in a random order" is the game's shuffle.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, PlayerFilter, PlayerScope, Value

YOU = PlayerFilter(PlayerScope.YOU)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    library = board.game.player(PlayerId(0)).library
    # Put a known creature on top of the Forests.
    bears = board.game.create_object(
        card_db.lookup("Grizzly Bears"), PlayerId(0), Zone.LIBRARY
    )
    library.remove(bears.id)
    library.insert(1, bears.id)
    board.bears = bears
    return board


def _run(board, *effects):
    resolution = Resolution(game=board.game, source=0, controller=PlayerId(0))
    execute(resolution, effects)
    return resolution


LOOK4 = Effect(EffectKind.LOOK_AT_TOP, players=YOU, amount=Value.of(4))


def test_looking_moves_nothing(board):
    before = list(board.game.player(PlayerId(0)).library)
    resolution = _run(board, LOOK4)
    assert board.game.player(PlayerId(0)).library == before
    assert resolution.pile == before[:4]


def test_a_description_picks_only_matching_cards(board):
    creature = ObjectFilter(from_pile=True, types_all=CardType.CREATURE, count=Value.of(1))
    before = set(board.game.player(PlayerId(0)).hand)
    _run(board, LOOK4, Effect(EffectKind.MOVE_ZONE, targets=creature, zone=Zone.HAND))
    hand = [i for i in board.game.player(PlayerId(0)).hand if i not in before]
    names = [board.game.printed_characteristics(board.game.objects[i]).name for i in hand]
    assert names == ["Grizzly Bears"]


def test_the_rest_goes_to_the_bottom_and_is_not_what_was_taken(board):
    player = board.game.player(PlayerId(0))
    top4 = list(player.library[:4])
    in_hand = len(player.hand)
    one = ObjectFilter(from_pile=True, count=Value.of(1))
    rest = ObjectFilter(from_pile=True)
    _run(
        board,
        LOOK4,
        Effect(EffectKind.MOVE_ZONE, targets=one, zone=Zone.HAND),
        Effect(EffectKind.PUT_ON_LIBRARY, targets=rest, keywords=("bottom",)),
    )
    assert len(player.hand) == in_hand + 1
    # The three left behind are now the bottom three, same objects (no zone change).
    assert player.library[-3:] == top4[1:]
    assert all(board.game.objects[i].is_live for i in top4[1:])


def test_put_on_the_bottom_is_the_bottom(board):
    player = board.game.player(PlayerId(0))
    top = player.library[0]
    _run(
        board,
        Effect(
            EffectKind.PUT_ON_LIBRARY,
            targets=ObjectFilter(specific=(top,), zones=frozenset({Zone.LIBRARY})),
            keywords=("bottom",),
        ),
    )
    assert player.library[-1] == top


def test_mill_makes_a_pile_of_the_milled_cards(board):
    resolution = _run(board, Effect(EffectKind.MILL, players=YOU, amount=Value.of(2)))
    graveyard = board.game.player(PlayerId(0)).graveyard
    assert sorted(resolution.pile) == sorted(graveyard)
