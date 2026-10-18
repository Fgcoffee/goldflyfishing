"""Whenever-trigger events read from oracle text, proved on a board.

Each test reads a trigger with the real parser, puts it on a permanent, makes
the event happen through the engine - and makes a near miss happen, to show
the trigger does *not* fire then (CR 603.2).
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def watch(board, text: str, holder: str = "Grizzly Bears", controller: int = 0):
    trigger = parse_trigger(Stream.of(text))
    assert trigger is not None, text
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add(holder, ability)
    obj = board.play(holder, controller)
    board.game.pending_triggers.clear()
    return obj


def fired(board, text: str) -> int:
    count = sum(1 for p in board.game.pending_triggers if p.ability.text == text)
    board.game.pending_triggers.clear()
    return count


def _lethal(board, *objs):
    for obj in objs:
        obj.damage = 100
    board.game.invalidate_characteristics()
    board.sba()


# ---------------------------------------------------------------------------
# CR 603.4: an intervening-if adds a check and takes nothing away
# ---------------------------------------------------------------------------


def test_an_intervening_if_keeps_the_batch(board):
    """CR 603.2c: "one or more ... die, if ..." is still one trigger for
    the creatures that died together. The if-clause rebuilt the condition
    without ``batched``, so a wipe triggered once per creature."""
    text = "Whenever one or more other creatures die, if it's your turn, draw a card."
    watch(board, text, holder="Llanowar Elves")
    board.game.active_player = PlayerId(0)
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    board.game.pending_triggers.clear()
    _lethal(board, a, b)
    assert fired(board, text) == 1


def test_an_intervening_if_keeps_the_ordinal():
    trigger = parse_trigger(
        Stream.of("Whenever you cast your second spell each turn, if it's your turn, draw a card.")
    )
    assert trigger.ordinal == 2
    assert not trigger.intervening_if.is_always


def test_an_intervening_if_after_a_disjunction_is_checked(board):
    """"A creature dies or a creature card leaves your graveyard, if ...":
    the if-clause is the whole trigger's. It was dropped with the
    alternatives, and then - once kept - asked of neither half."""
    text = (
        "Whenever another creature dies or a creature card leaves your graveyard, "
        "if it's your turn, draw a card."
    )
    watch(board, text, holder="Llanowar Elves")
    trigger = parse_trigger(Stream.of(text))
    assert len(trigger.alternatives) == 2

    board.game.active_player = PlayerId(1)
    a = board.play("Hill Giant", 1)
    board.game.pending_triggers.clear()
    _lethal(board, a)
    assert fired(board, text) == 0

    board.game.active_player = PlayerId(0)
    b = board.play("Hill Giant", 1)
    board.game.pending_triggers.clear()
    _lethal(board, b)
    assert fired(board, text) == 1
