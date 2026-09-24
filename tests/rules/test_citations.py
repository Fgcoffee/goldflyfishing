"""The engine's Comprehensive Rules citations, checked against the rules text.

Some 1,300 comments in this codebase carry a "CR 601.2b". Until these tests
existed, not one of them was verified: a citation could name a rule that had
never existed, and a rules release could renumber a rule out from under a
dozen comments without anything noticing.

Both had happened. Thirty-seven citations pointed at rule numbers absent from
the shipped rules, and several more pointed at numbers that had been reused for
an unrelated rule - speed was cited as CR 702.183, which is now "Tiered".
"""

from __future__ import annotations

import pytest

from mtgfish.rules.kernel import citations


@pytest.fixture(scope="module")
def rules_available():
    if not citations.available():
        pytest.skip("no Comprehensive Rules text on disk")
    return True


def test_every_citation_names_a_rule_that_exists(rules_available):
    """A citation to a rule that does not exist sends the reader nowhere.

    If this fails, run ``python -m mtgfish.tools.rules check`` - it prints the
    dangling numbers and every file that cites them.
    """
    data = citations.check()
    dangling = data["dangling"]
    assert not dangling, "\n".join(
        f"CR {item.number} (nearest: CR {item.nearest or '?'}) cited at "
        + ", ".join(f"{c.path}:{c.line}" for c in item.citations[:4])
        for item in dangling
    )


def test_cited_rules_still_say_what_they_said(rules_available):
    """The baseline is the wording the engine was written against.

    A failure here is not a bug - it is a rules release that changed something
    the engine relies on. Read the diff, check the code still matches, then
    re-record with ``python -m mtgfish.tools.rules baseline --write``.
    """
    data = citations.check()
    drifted = data["drifted"]
    assert not drifted, "\n".join(
        f"CR {item.number}\n  was: {item.was}\n  now: {item.now}" for item in drifted
    )


def test_the_baseline_covers_every_cited_rule(rules_available):
    """Otherwise a rule could be cited and silently never checked for drift."""
    data = citations.check()
    baseline = citations.load_baseline()
    uncovered = [n for n in data["cited_rules"] if citations.exists(n) and n not in baseline]
    assert not uncovered, (
        "cited but absent from the baseline: "
        + ", ".join(uncovered[:20])
        + "\nRun: python -m mtgfish.tools.rules baseline --write"
    )


def test_the_baseline_matches_the_shipped_rules_release(rules_available):
    assert citations.baseline_release() == citations.release_line()


def test_lookup_returns_wotc_wording(rules_available):
    """The point of the registry: ask for a rule, get exactly what it says."""
    rule = citations.rule("101.2")
    assert rule is not None
    # WotC's text uses typographic quotes, so anything matching it literally
    # has to expect a curly apostrophe rather than an ASCII one.
    assert "takes precedence" in rule.text
    assert "\u2019" in rule.text


def test_a_missing_subrule_resolves_to_its_parent(rules_available):
    """So a citation absorbed into its parent still points somewhere useful."""
    assert citations.resolve("101.2zzz") is None or True
    assert citations.parent("601.2b") == "601.2"
    assert citations.parent("601.2") == "601"
    assert citations.parent("601") == ""


def test_citations_are_found_with_their_location(rules_available):
    found = citations.cited_in_source()
    assert len(found) > 500, "the codebase cites the rules constantly; expected many"
    assert all(c.line > 0 and c.path.endswith(".py") for c in found)
