"""Costs that name their object with a pronoun or an attachment (CR 602.1).

"Remove three quest counters from this enchantment and sacrifice it": the
only object the cost names is this permanent, so "it" is this permanent. A
pronoun elsewhere in a cost has no referent the payment can charge, and the
cost stays unread rather than letting the player sacrifice anything at all.
"""

from __future__ import annotations

from mtgfish.parser.costs import parse_cost
from mtgfish.rules.cr100_game_concepts.cr118_costs import CostKind


def test_it_after_this_permanent_is_this_permanent():
    cost, reason = parse_cost("Remove three quest counters from this enchantment and sacrifice it")
    assert cost is not None, reason
    sacrifice = cost.components[-1]
    assert sacrifice.kind is CostKind.SACRIFICE
    assert sacrifice.filter.source_only


def test_exile_it_and_return_it_after_this_permanent():
    exile, reason = parse_cost("{T}, Remove X time counters from this artifact and exile it")
    assert exile is not None, reason
    assert exile.components[-1].filter.source_only
    back, reason = parse_cost("{1}, Remove an eon counter from this land and return it to its owner's hand")
    assert back is not None, reason
    assert back.components[-1].kind is CostKind.RETURN_TO_HAND
    assert back.components[-1].filter.source_only


def test_a_pronoun_with_no_named_permanent_is_not_read():
    cost, _ = parse_cost("{2}, sacrifice it")
    assert cost is None


def test_enchanted_creature_is_one_object():
    cost, reason = parse_cost("Sacrifice enchanted creature")
    assert cost is not None, reason
    assert cost.components[0].amount.constant == 1


def test_all_permanents_is_still_not_charged_as_one():
    cost, _ = parse_cost("Sacrifice all permanents you control")
    assert cost is None
