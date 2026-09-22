"""Naming a card (CR 201.4).

"Choose a card name" had nowhere to put the answer. The engine could choose a
creature type or a colour and remember it, but not a name - so every card that
names one had nothing to compare against.

CR 201.4 is deliberately permissive: any name in the Oracle reference is
legal, whether or not a card with that name is anywhere in the game. Naming a
card the opponent has not cast yet is the usual case, not an edge one.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import ObjectFilter

NAME_A_CARD = Effect(
    EffectKind.CHOOSE_QUALITY, keywords=("card name",), text="choose a card name"
)
OF_THE_CHOSEN_NAME = ObjectFilter(of_chosen_name=True)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


class Names:
    def __init__(self, name):
        self.name = name
        self.asked = []

    def choose_quality(self, game, player, kind):
        self.asked.append(kind)
        return self.name


def _choose(board, source, agent=None):
    if agent is not None:
        board.game.agents[0] = agent
    execute(
        Resolution(game=board.game, source=source.id, controller=PlayerId(0)),
        (NAME_A_CARD,),
    )


# ---------------------------------------------------------------------------
# Making the choice
# ---------------------------------------------------------------------------


def test_the_chosen_name_is_recorded_on_the_source(board):
    """CR 201.4: the choice belongs to the object that asked for it, which is
    what every later "the chosen name" reads."""
    source = board.play("Sol Ring", controller=0)
    _choose(board, source, Names("Lightning Bolt"))

    assert source.chosen_name == "Lightning Bolt"


def test_the_controller_is_asked_for_a_card_name(board):
    """Not a creature type and not a colour - the kind is part of the ask."""
    source = board.play("Sol Ring", controller=0)
    agent = Names("Lightning Bolt")
    _choose(board, source, agent)

    assert agent.asked == ["card name"]


def test_a_name_no_card_in_the_game_has_is_still_a_legal_choice(board):
    """CR 201.4 asks for a name in the Oracle reference, not a name present in
    this game. Naming what the opponent has not cast yet is the point."""
    source = board.play("Sol Ring", controller=0)
    _choose(board, source, Names("Armageddon"))

    assert source.chosen_name == "Armageddon"


def test_with_no_agent_a_name_is_still_chosen(board):
    """A choice that has to be made gets made: the deterministic stand-in is
    a name an opponent actually has, which keeps a replay reproducible."""
    source = board.play("Sol Ring", controller=0)
    board.play("Grizzly Bears", controller=1)
    _choose(board, source)

    assert source.chosen_name == "Grizzly Bears"


def test_choosing_a_name_leaves_the_other_qualities_alone(board):
    """The control: three choices share one opcode and must not bleed."""
    source = board.play("Sol Ring", controller=0)
    _choose(board, source, Names("Lightning Bolt"))

    assert source.chosen_type == ""
    assert source.chosen_color == 0


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def test_a_filter_matches_the_named_card(board):
    """CR 201.2a: names are compared as names."""
    source = board.play("Sol Ring", controller=0)
    bear = board.play("Grizzly Bears", controller=1)
    _choose(board, source, Names("Grizzly Bears"))

    assert matches(board.game, bear, OF_THE_CHOSEN_NAME, source=source.id)


def test_a_filter_does_not_match_a_different_card(board):
    source = board.play("Sol Ring", controller=0)
    bear = board.play("Grizzly Bears", controller=1)
    _choose(board, source, Names("Runeclaw Bear"))

    assert not matches(board.game, bear, OF_THE_CHOSEN_NAME, source=source.id)


def test_nothing_matches_before_a_name_is_chosen(board):
    """An unasked filter that matched everything would be far worse than one
    that matches nothing."""
    source = board.play("Sol Ring", controller=0)
    bear = board.play("Grizzly Bears", controller=1)

    assert not matches(board.game, bear, OF_THE_CHOSEN_NAME, source=source.id)


def test_a_filter_with_no_chooser_matches_nothing(board):
    """Fails closed, the way every unanswerable constraint in this module
    does."""
    bear = board.play("Grizzly Bears", controller=1)

    assert not matches(board.game, bear, OF_THE_CHOSEN_NAME, source=99999)


def test_an_ordinary_filter_is_unaffected(board):
    """The control: a filter that does not ask about the chosen name still
    matches what it always did."""
    bear = board.play("Grizzly Bears", controller=1)

    assert matches(board.game, bear, ObjectFilter(named=("Grizzly Bears",)))
