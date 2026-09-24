"""A card the engine cannot represent is illegal, not fatal.

``UnknownManaSymbol`` is deliberately fatal where it is raised: a set
introducing a new symbol must surface rather than quietly produce a card that
costs less than it should. But raising it out of the middle of a full-pool
iteration aborted the whole pass, so one unmodellable card made every
whole-pool report impossible to run rather than one card short.

Such a card is now absent from the pool - no deck can contain it and no sweep
trips over it - and every exclusion is recorded with its reason, so the engine
can still say what it is not modelling.
"""

from __future__ import annotations


def test_a_full_sweep_completes(card_db):
    """The whole point: one bad card must not abort the iteration."""
    count = sum(1 for _ in card_db.iter_cards())
    assert count > 30000


def test_exclusions_are_recorded_with_a_reason(card_db):
    for _ in card_db.iter_cards():
        pass
    for oracle_id, reason in card_db.unrepresentable.items():
        assert oracle_id
        assert reason, "an exclusion with no reason is a silent one"


def test_an_excluded_card_is_not_in_the_pool(card_db):
    """Illegal means absent: lookup finds nothing, so no deck can play it."""
    for _ in card_db.iter_cards():
        pass
    for oracle_id in card_db.unrepresentable:
        assert card_db.by_oracle_id(oracle_id) is None


def test_the_pool_is_almost_entirely_representable(card_db):
    """A guard on the exclusion list, so it cannot quietly grow.

    If this fails, the engine has stopped modelling something it used to -
    read ``unrepresentable`` for what and why.
    """
    total = sum(1 for _ in card_db.iter_cards())
    assert len(card_db.unrepresentable) < max(10, total // 1000)
