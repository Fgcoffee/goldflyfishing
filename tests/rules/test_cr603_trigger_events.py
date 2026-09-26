"""Trigger events the grammar reads, proved on a board (CR 603).

A trigger is only real if the engine emits the event it listens for, with
the fields it filters on. Each test here reads a trigger condition from
oracle text with the real parser, puts it on a permanent, makes the event
happen - and also makes a near miss happen, to show it does *not* fire then.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import Zone


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def watch(board, text: str, holder: str = "Grizzly Bears", controller: int = 0):
    """Put a permanent carrying a trigger read from ``text`` on the board."""
    stream = Stream.of(text)
    trigger = parse_trigger(stream)
    assert trigger is not None, text
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add(holder, ability)
    return board.play(holder, controller)


def fired(board, text: str) -> int:
    count = sum(1 for p in board.game.pending_triggers if p.ability.text == text)
    board.game.pending_triggers.clear()
    return count


# ---------------------------------------------------------------------------
# CR 603.10a: a permanent's own leaves-the-battlefield abilities look back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "When this creature dies, draw a card.",
        "When this creature leaves the battlefield, draw a card.",
        "When this creature is put into a graveyard from the battlefield, draw a card.",
    ],
)
def test_a_card_triggers_on_its_own_departure(board, text):
    """The card that died is a new object in the graveyard (CR 400.7), and
    its abilities work on the battlefield - so nobody answered for it, and
    every "when this dies" in the pool did nothing."""
    me = watch(board, text)
    board.game.pending_triggers.clear()
    actions.destroy(board.game, me)
    assert fired(board, text) == 1


def test_a_departed_card_is_controlled_by_its_last_controller(board):
    text = "When this creature dies, draw a card."
    me = watch(board, text, controller=1)
    board.game.pending_triggers.clear()
    actions.destroy(board.game, me)
    (pending,) = board.game.pending_triggers
    assert pending.controller == 1


def test_another_permanent_dying_does_not_fire_a_self_trigger(board):
    text = "When this creature dies, draw a card."
    watch(board, text)
    other = board.play("Llanowar Elves", 0)
    board.game.pending_triggers.clear()
    actions.destroy(board.game, other)
    assert fired(board, text) == 0


def test_exile_is_not_dying(board):
    text = "When this creature dies, draw a card."
    me = watch(board, text)
    board.game.pending_triggers.clear()
    actions.exile(board.game, me)
    assert fired(board, text) == 0


# ---------------------------------------------------------------------------
# CR 603.6c: "put into a graveyard from anywhere" is the arrival itself
# ---------------------------------------------------------------------------


def test_put_into_a_graveyard_from_anywhere_fires_for_a_milled_card(board):
    text = "Whenever a creature card is put into a graveyard from anywhere, draw a card."
    watch(board, text)
    board.game.pending_triggers.clear()
    library = board.game.player(1).library
    top = board.game.objects[library[0]]
    # Every library in the harness is Forests: make the top card a creature.
    board.game.move_object(top, Zone.GRAVEYARD)
    assert fired(board, text) == 0  # a Forest is not a creature card
    creature = board.graveyard("Hill Giant", 1)
    board.game.move_object(creature, Zone.LIBRARY, to_top=True)
    board.game.pending_triggers.clear()
    actions.mill(board.game, 1, 1)
    assert fired(board, text) == 1


def test_a_dying_creature_card_is_also_put_into_a_graveyard(board):
    text = "Whenever a creature card is put into a graveyard from anywhere, draw a card."
    watch(board, text)
    victim = board.play("Hill Giant", 1)
    board.game.pending_triggers.clear()
    actions.destroy(board.game, victim)
    assert fired(board, text) == 1


# ---------------------------------------------------------------------------
# CR 603.10a: "leaves your graveyard" asks about the card as it was there
# ---------------------------------------------------------------------------


def test_a_card_leaving_a_graveyard_is_seen_as_it_was_there(board):
    text = "Whenever a creature card leaves your graveyard, draw a card."
    watch(board, text)
    card = board.graveyard("Hill Giant", 0)
    board.game.pending_triggers.clear()
    board.game.move_object(card, Zone.EXILE)
    assert fired(board, text) == 1


def test_a_card_leaving_hand_is_not_leaving_a_graveyard(board):
    text = "Whenever a creature card leaves your graveyard, draw a card."
    watch(board, text)
    card = board.hand("Hill Giant", 0)
    board.game.pending_triggers.clear()
    board.game.move_object(card, Zone.EXILE)
    assert fired(board, text) == 0
