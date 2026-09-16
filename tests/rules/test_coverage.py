"""The rules-coverage registry must stay complete and honest.

Same guarantee as the keyword registry, applied to the Comprehensive Rules
themselves: the checklist comes from WotC, not from this codebase, so the
engine cannot score itself on a list it wrote. A rules update that adds a rule
group fails this suite by number.

The ratchet test is the important one. Coverage may go up; it may not quietly
go down because a refactor dropped something.
"""

from __future__ import annotations

import pytest

from mtgfish.data.comprehensive_rules import default_path, parse
from mtgfish.rules.coverage import COVERAGE, Status, entry_for, report

#: The core sections, which is what the engine's correctness rests on.
CORE_SECTIONS = range(1, 7)

#: Current measured coverage of the 100s-600s. Raise it as rules land; never
#: lower it to make a failing build pass.
CORE_COVERAGE_FLOOR = 88.0


@pytest.fixture(scope="module")
def rules():
    path = default_path()
    if not path.exists():
        pytest.skip(
            f"Comprehensive Rules not cached ({path}); "
            "run python -m mtgfish.tools.rules_coverage"
        )
    return parse(path)


def test_the_rules_parse(rules):
    """A parse that silently finds 147 rules instead of 3,300 would make every
    coverage number meaningless."""
    assert len(rules) > 3000
    numbers = {r.number for r in rules}
    for expected in ("100.1", "302.6", "305.7", "400.7", "601.2f", "613.8a", "704.5g"):
        assert expected in numbers


def test_903_9_is_split_the_way_the_engine_models_it(rules):
    """The engine treats graveyard/exile as a state-based action and
    hand/library as a replacement. Check that against the actual rules text, so
    a future rules change to 903.9 surfaces here rather than in a wrong game."""
    by_number = {r.number: r.text for r in rules}

    sba_rule = by_number.get("903.9a", "")
    assert "graveyard" in sba_rule and "exile" in sba_rule
    assert "state-based action" in sba_rule.lower()

    replacement_rule = by_number.get("903.9b", "")
    assert "hand" in replacement_rule and "library" in replacement_rule
    assert "instead" in replacement_rule


def test_every_core_rule_group_is_classified(rules):
    """Nothing in the 100s-600s may be unclassified.

    An unclassified group defaults to NOT_IMPLEMENTED, so this cannot flatter
    the score - but leaving one unlisted means nobody decided about it, and
    deciding is the point.
    """
    unclassified = sorted(
        {
            rule.rule_group
            for rule in rules
            if rule.section in CORE_SECTIONS and rule.rule_group not in COVERAGE
        }
    )
    assert not unclassified, (
        f"{len(unclassified)} rule groups in the core sections are unclassified: "
        f"{unclassified}. Add them to mtgfish/rules/coverage.py."
    )


def test_core_coverage_does_not_regress(rules):
    data = report(rules)
    done = sum(data["sections"][s]["done"] for s in CORE_SECTIONS if s in data["sections"])
    relevant = sum(
        data["sections"][s]["relevant"] for s in CORE_SECTIONS if s in data["sections"]
    )
    percent = 100.0 * done / relevant

    assert percent >= CORE_COVERAGE_FLOOR, (
        f"core rules coverage fell to {percent:.1f}% (floor {CORE_COVERAGE_FLOOR}%). "
        "Raise the floor when coverage improves; do not lower it."
    )


def test_zones_and_turn_structure_are_complete(rules):
    """The two sections claimed as fully done had better be."""
    data = report(rules)
    for section in (4, 5):
        assert data["sections"][section]["percent"] == 100.0


def test_partial_entries_explain_themselves(rules):
    """A PARTIAL with no note is a claim nobody can check."""
    missing = [
        group
        for group, entry in COVERAGE.items()
        if entry.status is Status.PARTIAL and not entry.note
    ]
    assert not missing, f"PARTIAL entries with no explanation: {missing}"


def test_specific_subrules_override_their_group(rules):
    """A more specific key must win over its group.

    Otherwise a group-level mark would score every subrule under it as done,
    which is the flattering arithmetic this module exists to prevent. The
    mechanism is checked with a synthetic pair rather than a real gap, so that
    closing gaps does not break the test that guards the arithmetic.
    """
    from mtgfish.rules.coverage import COVERAGE, Entry

    assert entry_for("601.2f").status is Status.IMPLEMENTED
    assert entry_for("601").status is Status.IMPLEMENTED

    COVERAGE["601.99"] = Entry(Status.NOT_IMPLEMENTED, "", "a synthetic gap")
    try:
        assert entry_for("601.99").status is Status.NOT_IMPLEMENTED
        assert entry_for("601.99a").status is Status.NOT_IMPLEMENTED
        # ...and its sibling is untouched.
        assert entry_for("601.2f").status is Status.IMPLEMENTED
    finally:
        del COVERAGE["601.99"]
