"""What a permanent was, for anything that reads it after it died (CR 704.8).

"If a state-based action results in a permanent leaving the battlefield at the
same time other state-based actions were performed, that permanent's last known
information is derived from the game state before any of those state-based
actions were performed."

Counter annihilation (CR 704.5q) ran before the permanents moved, so a
creature that died in the same round reached the graveyard with its counters
already rewritten. The rule's own example is the one that bites: a creature
with one +1/+1 counter and three -1/-1 counters cancels a pair, arrives in the
graveyard carrying no +1/+1 counter, and undying - which asks exactly that -
brings it back. It should stay dead.

Its counters are simply left alone. They cannot matter to anything except as
the record of what it was.
"""

from __future__ import annotations

from harness import make_board


def _died(board, obj):
    return obj.id not in board.game.battlefield


def test_a_creature_that_dies_keeps_the_counters_it_had(card_db):
    """The rule's own example, in the shape that made it matter."""
    board = make_board(card_db)
    creature = board.play("Grizzly Bears")
    creature.counters.update({"+1/+1": 1, "-1/-1": 3})
    board.refresh()

    board.sba()

    assert _died(board, creature)
    assert creature.counters["+1/+1"] == 1, "undying asks whether it had one"
    assert creature.counters["-1/-1"] == 3


def test_a_survivor_still_has_its_counters_cancelled(card_db):
    """CR 704.5q is untouched for anything that stays on the battlefield."""
    board = make_board(card_db)
    creature = board.play("Grizzly Bears")
    creature.counters.update({"+1/+1": 2, "-1/-1": 1})
    board.refresh()

    board.sba()

    assert not _died(board, creature)
    assert creature.counters.get("+1/+1", 0) == 1
    assert creature.counters.get("-1/-1", 0) == 0


def test_cancelling_can_still_save_a_creature(card_db):
    """Annihilation happens first for a survivor, so it is not lethal."""
    board = make_board(card_db)
    creature = board.play("Grizzly Bears")
    creature.counters.update({"+1/+1": 2, "-1/-1": 2})
    board.refresh()

    board.sba()

    assert not _died(board, creature)
    assert board.pt(creature) == (2, 2)


def test_a_creature_with_only_minus_counters_dies_with_them_intact(card_db):
    board = make_board(card_db)
    creature = board.play("Grizzly Bears")
    creature.counters["-1/-1"] = 2
    board.refresh()

    board.sba()

    assert _died(board, creature)
    assert creature.counters["-1/-1"] == 2
