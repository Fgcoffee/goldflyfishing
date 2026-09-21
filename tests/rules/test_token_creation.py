"""How many tokens an effect makes, and who gets them (CR 111.1, 111.2).

Two bugs lived in one line of ``_do_create_token``.

The count guarded on ``effect.amount.constant``, which holds the *value* a
literal carries rather than a flag saying the amount is a literal. Every
non-literal amount therefore read as zero, which is falsy, and fell through to
one token - so Secure the Wastes for X=6 made one Soldier and Avenger of
Zendikar made one Plant.

The controller was always the resolving player, whatever the effect said, so
"target opponent creates a 1/1" handed the token to the caster. Forbidden
Orchard became a strictly better land and the Hunted cycle lost its drawback.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, _do_create_token
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope, ValueKind
from mtgfish.rules.kernel.values import Value

SOLDIER = TokenSpec(
    types=CardType.CREATURE,
    subtypes=("Soldier",),
    power=Value(constant=1),
    toughness=Value(constant=1),
)


def _make(board, amount=None, players=None, x_value=0, controller=0):
    effect = Effect(
        EffectKind.CREATE_TOKEN,
        token=SOLDIER,
        amount=amount if amount is not None else Value(constant=0),
        players=players,
    )
    resolution = Resolution(
        game=board.game,
        source=NO_OBJECT,
        controller=PlayerId(controller),
        x_value=x_value,
    )
    _do_create_token(resolution, effect)
    return resolution


def _tokens(board, controller):
    return [
        o
        for o in board.game.permanents(PlayerId(controller))
        if board.game.characteristics(o).has_subtype("Soldier")
    ]


# -- how many ---------------------------------------------------------------


def test_no_stated_count_makes_one(card_db):
    """"Create a 1/1 Soldier" arrives with a literal zero, meaning unstated."""
    board = make_board(card_db)
    _make(board)
    assert len(_tokens(board, 0)) == 1


def test_a_literal_count_is_honoured(card_db):
    board = make_board(card_db)
    _make(board, amount=Value(constant=3))
    assert len(_tokens(board, 0)) == 3


def test_x_makes_x_tokens(card_db):
    """Secure the Wastes for X=6 is six Soldiers, not one."""
    board = make_board(card_db)
    _make(board, amount=Value(kind=ValueKind.X), x_value=6)
    assert len(_tokens(board, 0)) == 6


def test_x_of_zero_makes_none(card_db):
    """An evaluated zero is a real zero, unlike an unstated count."""
    board = make_board(card_db)
    _make(board, amount=Value(kind=ValueKind.X), x_value=0)
    assert _tokens(board, 0) == []


# -- for whom ---------------------------------------------------------------


def test_tokens_go_to_the_resolving_player_by_default(card_db):
    board = make_board(card_db)
    _make(board, amount=Value(constant=2))
    assert len(_tokens(board, 0)) == 2
    assert _tokens(board, 1) == []


def test_each_opponent_creates_their_own(card_db):
    """CR 111.2: the player who creates a token controls it."""
    board = make_board(card_db)
    _make(board, players=PlayerFilter(scope=PlayerScope.EACH_OPPONENT))
    assert _tokens(board, 0) == []
    assert len(_tokens(board, 1)) == 1


def test_each_player_creates_their_own(card_db):
    board = make_board(card_db)
    _make(board, amount=Value(constant=2), players=PlayerFilter(scope=PlayerScope.EACH_PLAYER))
    assert len(_tokens(board, 0)) == 2
    assert len(_tokens(board, 1)) == 2
