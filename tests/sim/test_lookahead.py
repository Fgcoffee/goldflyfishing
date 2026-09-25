"""Looking ahead: a bot plays an action out on a copy before making it.

The point is for a bot to see what a play leads to - the spell resolving, the
permanent entering, the trigger that draws a card - and to pass over a play the
engine would refuse or one that changes nothing. What it must never do is
change the real game, or see a card it could not know.
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.ai.lookahead import project
from mtgfish.ai.simple import SimpleAgent
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.kernel.enums import Phase


@pytest.fixture
def board(card_db):
    from mtgfish.parser.compile import OracleAbilities

    board = make_board(card_db, OracleAbilities())
    board.game.phase = Phase.PRECOMBAT_MAIN
    for player in board.game.players:
        board.game.agents[player.id] = SimpleAgent(lookahead=False)
    for _ in range(10):
        board.game.player(0).library.append(board.hand("Forest").id)
        board.game.player(0).hand.pop()
    return board


def cast_action(card) -> Action:
    return Action(ActionKind.CAST_SPELL, source=card.id)


def fingerprint(game):
    return (
        game.log.digest(),
        game.rng.getstate(),
        tuple(game.player(0).library),
        tuple(game.player(0).hand),
        tuple(game.battlefield),
        tuple(o.tapped for o in game.permanents()),
        tuple(p.life for p in game.players),
        len(game.objects),
    )


def test_looking_ahead_leaves_the_real_game_alone(board):
    """Not a card, a tap, a random number or a line of the log."""
    for _ in range(3):
        board.play("Island")
    divination = board.hand("Divination")
    before = fingerprint(board.game)

    project(board.game, 0, cast_action(divination))

    assert fingerprint(board.game) == before


def test_a_draw_is_seen_as_a_draw(board):
    for _ in range(3):
        board.play("Island")
    divination = board.hand("Divination")

    projection = project(board.game, 0, cast_action(divination))

    assert projection.legal and projection.changed
    assert projection.players[0].cards_drawn == 2


def test_what_a_permanent_sets_off_is_seen_too(board):
    """Casting a creature, a card drawn off it, and the creature entering - the chain
    the bot could not see when it judged a spell by its card alone."""
    board.play("Beast Whisperer")
    board.play("Forest")
    board.play("Forest")
    bears = board.hand("Grizzly Bears")

    projection = project(board.game, 0, cast_action(bears))

    assert projection.entered[0] == ["Grizzly Bears"]
    assert projection.resolutions >= 2
    assert projection.players[0].cards_drawn == 1


def test_an_action_the_engine_would_refuse_is_seen_as_refused(board):
    bolt = board.hand("Lightning Bolt")

    assert not project(board.game, 0, cast_action(bolt)).legal


def test_features_have_a_fixed_shape(board):
    """A model reads these; their length cannot depend on the board."""
    for _ in range(3):
        board.play("Island")
    divination = board.hand("Divination")
    bolt = board.hand("Lightning Bolt")

    drawn = project(board.game, 0, cast_action(divination)).features(0)
    refused = project(board.game, 0, cast_action(bolt)).features(0)

    assert len(drawn) == len(refused)
