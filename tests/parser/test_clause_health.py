"""A clause that raises must not look like a card the grammar cannot read.

``parse_effect`` catches everything a clause throws and moves on, which is the
right behaviour in a run - one broken rule should not take down a whole
simulation. But it means a NameError in a new clause is indistinguishable from
an ability the grammar legitimately does not cover: coverage quietly drops and
nothing says why.

That happened. A new clause referenced an undefined name, every card using it
reported "not fully consumed", and the bug was invisible until it was looked
for by hand.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import clauses, parse_card


@pytest.fixture
def strict():
    """Make clause exceptions propagate for the duration of a test."""
    clauses.CLAUSE_ERRORS.clear()
    clauses.STRICT_CLAUSES = True
    try:
        yield
    finally:
        clauses.STRICT_CLAUSES = False
        clauses.CLAUSE_ERRORS.clear()


def test_no_clause_raises_on_the_cards_people_play(card_db, strict):
    """The whole grammar, over the cards that actually matter.

    Deliberately run with exceptions propagating: a clause that throws is a
    bug in the parser, not a limit of it, and the two must not be reported the
    same way.
    """
    card_db.registry()
    for card in card_db.iter_by_play_rate(limit=2000):
        parse_card(card)


def test_clause_errors_are_counted_when_not_strict(card_db, monkeypatch):
    """The counter is what makes the failure visible in a normal run."""
    clauses.CLAUSE_ERRORS.clear()

    def explode(stream):
        raise RuntimeError("boom")

    monkeypatch.setattr(clauses, "CLAUSES", [("exploding", explode)])
    from mtgfish.parser.tokens import Stream

    assert clauses.parse_effect(Stream.of("draw a card")) is None
    assert clauses.CLAUSE_ERRORS["exploding"] == 1
    clauses.CLAUSE_ERRORS.clear()
