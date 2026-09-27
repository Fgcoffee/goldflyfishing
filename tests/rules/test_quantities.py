"""How many objects an effect acts on (CR 107.1c, 608.2d, 701.23).

"Sacrifice another creature" is one creature other than the source, chosen
by the player as the effect resolves - not every other creature. "Any number
of" is the player's choice, zero included. A search finds as many as it says,
and puts them where it says.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=2)


def creatures(board, player: int = 0) -> list:
    game = board.game
    return [
        game.objects[oid]
        for oid in game.battlefield
        if game.objects[oid].controller == PlayerId(player)
        and game.characteristics(game.objects[oid]).is_creature
    ]


def resolve(board, source, text: str) -> None:
    execute(
        Resolution(game=board.game, source=source.id, controller=P0),
        tuple(parse_effects(Stream.of(text))),
    )


def test_sacrifice_another_creature_takes_one_other(board):
    source = board.play("Grizzly Bears")
    board.play("Grizzly Bears")
    board.play("Grizzly Bears")
    resolve(board, source, "Sacrifice another creature.")

    left = creatures(board)
    assert len(left) == 2
    assert source in left


class Picky:
    """An agent that picks exactly the objects it is told to."""

    def __init__(self, keep: int) -> None:
        self.keep = keep

    def choose_objects(self, game, player_id, effect, candidates, wanted):
        return candidates[: self.keep]


def test_any_number_of_is_the_players_choice(board):
    source = board.play("Sol Ring")
    for _ in range(3):
        board.play("Grizzly Bears")
    board.game.agents[P0] = Picky(1)
    resolve(board, source, "Sacrifice any number of creatures.")

    assert len(creatures(board)) == 2


def test_any_number_of_may_be_none(board):
    source = board.play("Sol Ring")
    for _ in range(2):
        board.play("Grizzly Bears")
    board.game.agents[P0] = Picky(0)
    resolve(board, source, "Return any number of creatures you control to their owner's hand.")

    assert len(creatures(board)) == 2


# -- search (CR 701.23) ---------------------------------------------------------


def lands(board, player: int = 0) -> list:
    game = board.game
    return [
        game.objects[oid]
        for oid in game.battlefield
        if game.objects[oid].controller == PlayerId(player)
        and game.characteristics(game.objects[oid]).is_land
    ]


def test_search_up_to_two_finds_two(board):
    source = board.play("Sol Ring")
    resolve(
        board,
        source,
        "Search your library for up to two basic land cards, put them onto the "
        "battlefield tapped, then shuffle.",
    )
    found = lands(board)
    assert len(found) == 2
    assert all(obj.tapped for obj in found)


def test_one_onto_the_battlefield_and_the_other_into_hand(board):
    source = board.play("Sol Ring")
    hand_before = len(board.game.player(P0).hand)
    resolve(
        board,
        source,
        "Search your library for up to two basic land cards, reveal those cards, put one "
        "onto the battlefield tapped and the other into your hand, then shuffle.",
    )
    assert len(lands(board)) == 1 and lands(board)[0].tapped
    assert len(board.game.player(P0).hand) == hand_before + 1


def test_a_tutor_to_the_top_leaves_the_card_on_top(board):
    source = board.play("Sol Ring")
    wanted = board._place("Grizzly Bears", 0, Zone.LIBRARY)
    hand_before = len(board.game.player(P0).hand)
    resolve(
        board,
        source,
        "Search your library for a creature card, reveal it, then shuffle and put that card on top.",
    )
    library = board.game.player(P0).library
    assert library[0] == wanted.id
    assert len(board.game.player(P0).hand) == hand_before


# -- "where X is" (CR 107.3) ------------------------------------------------------


def test_minus_x_uses_the_defined_x(board):
    source = board.play("Sol Ring")
    board.play("Forest")
    bears = board.play("Grizzly Bears", controller=1)
    resolve(
        board,
        source,
        "Each creature gets -X/-X until end of turn, where X is the number of lands you control.",
    )
    board.refresh()
    assert board.pt(bears) == (1, 1)
