"""Ownership of tokens and copies (CR 108.3, 110.2, 111.2, 112.2, 400.3).

A card's owner is who brought it to the game; a token has no card, so its
owner is the player who created it (CR 111.2), and a copy of a spell is owned
by the player under whose control it was put on the stack (CR 707.10, 112.2).
Owner is what "you own" asks and where a card that changes zones goes
(CR 400.3), whoever controls it at the time.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import EACH_OPPONENT, ControllerRelation, ObjectFilter

SPIRIT = TokenSpec(types=CardType.CREATURE, subtypes=("Spirit",))


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def tokens(board):
    return [o for o in board.game.permanents() if o.kind is ObjectKind.TOKEN]


def test_a_token_is_owned_by_the_player_who_created_it(board):
    """CR 111.2: "target opponent creates a token" - it is theirs, not the
    caster's, in both ownership and control."""
    execute(
        Resolution(game=board.game, source=0, controller=PlayerId(0)),
        (Effect(EffectKind.CREATE_TOKEN, token=SPIRIT, players=EACH_OPPONENT),),
    )
    (token,) = tokens(board)
    assert token.owner == PlayerId(1)
    assert token.controller == PlayerId(1)


def test_a_stolen_token_is_still_owned_by_its_creator(board):
    """CR 108.4a with 111.2: control changes; ownership never does."""
    execute(
        Resolution(game=board.game, source=0, controller=PlayerId(0)),
        (Effect(EffectKind.CREATE_TOKEN, token=SPIRIT),),
    )
    (token,) = tokens(board)
    token.controller = PlayerId(1)
    board.refresh()
    you_own = ObjectFilter(owner=ControllerRelation.YOU)
    assert matches(board.game, token, you_own, controller=PlayerId(0))
    assert not matches(board.game, token, you_own, controller=PlayerId(1))


def test_a_copy_of_a_spell_is_owned_by_whoever_put_it_on_the_stack(board):
    """CR 707.10, 112.2: and the token it becomes as it resolves (CR 707.10f)
    keeps that owner."""
    original = board.game.create_object(
        board.db.lookup("Grizzly Bears"), PlayerId(0), Zone.STACK
    )
    copy = copy_spell(board.game, original, PlayerId(1))
    assert copy.owner == PlayerId(1)
    assert copy.controller == PlayerId(1)

    resolve_top(board.game)
    permanent = board.game.objects[copy.superseded_by]
    assert permanent.kind is ObjectKind.TOKEN
    assert permanent.owner == PlayerId(1)
    assert permanent.controller == PlayerId(1)


def test_a_card_goes_to_its_owners_graveyard_whoever_says_otherwise(board):
    """CR 400.3: a card of player 1's sent to player 0's graveyard goes to
    player 1's."""
    theirs = board.play("Grizzly Bears", controller=1)
    theirs.controller = PlayerId(0)
    moved = board.game.move_object(theirs, Zone.GRAVEYARD, to_player=PlayerId(0))
    assert moved.id in board.game.player(PlayerId(1)).graveyard
    assert moved.id not in board.game.player(PlayerId(0)).graveyard


def test_a_card_still_goes_to_the_battlefield_under_the_named_player(board):
    """A control: CR 400.3 is about hands, libraries and graveyards. Putting a
    card onto the battlefield under your control is not overruled."""
    theirs = board.graveyard("Grizzly Bears", controller=1)
    moved = board.game.move_object(theirs, Zone.BATTLEFIELD, to_player=PlayerId(0))
    assert moved.controller == PlayerId(0)
    assert moved.owner == PlayerId(1)
