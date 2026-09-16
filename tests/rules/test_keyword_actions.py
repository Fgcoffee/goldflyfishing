"""Keyword actions (CR 701).

The registry is checked against Scryfall's own catalog in both directions. The
forward check - every catalogued action is known - is the coverage number. The
reverse check - every builder names a real action - is the one that matters,
because it is the only thing that catches a plausible-sounding verb that Magic
does not actually have. It has caught one already.
"""

from __future__ import annotations

from mtgfish.rules import keywords
from mtgfish.rules.effects import EffectKind
from mtgfish.rules.keyword_actions import (
    ActionInstance,
    BUILDERS,
    build,
    implemented_actions,
)
from mtgfish.rules.keywords import Status


def test_every_builder_names_a_real_keyword_action():
    """The reverse check.

    A builder for something Magic has never printed is worse than a missing
    one: it looks like coverage, it passes every test written against it, and
    nothing else in the system can tell the difference.
    """
    catalogued = {name.lower() for name in keywords.KEYWORD_ACTIONS}
    invented = sorted(name for name in BUILDERS if name not in catalogued)
    assert not invented, f"not real keyword actions: {invented}"


def test_the_registry_covers_most_of_the_catalog():
    """The forward check, as a floor rather than a target."""
    catalogued = {name.lower() for name in keywords.KEYWORD_ACTIONS}
    covered = catalogued & implemented_actions()
    assert len(covered) >= len(catalogued) * 0.5


def test_an_unknown_action_is_unparsed_not_silent():
    """The whole registry exists so that a gap is loud."""
    effects = build("Blorp")
    assert len(effects) == 1
    assert effects[0].kind is EffectKind.UNPARSED
    assert "Blorp" in effects[0].text


def test_status_is_derived_from_what_the_builder_produces():
    """Not from a flag someone remembered to set.

    A builder that returns a correctly shaped effect wrapped around UNPARSED is
    partial, not implemented, and the registry has to say so on its own.
    """
    for name in sorted(implemented_actions()):
        spec = keywords.lookup(name)
        assert spec is not None, name
        effects = build(name, amount=1)
        unparsed = any(node.is_unparsed for e in effects for node in e.walk())
        if unparsed:
            assert spec.status is Status.PARTIAL, name
        else:
            assert spec.status is Status.IMPLEMENTED, name


def test_scry_and_surveil_are_the_same_shape_but_not_the_same_opcode():
    """CR 701.18 and 701.43 differ only in where the rejected cards go, and
    conflating them would quietly turn every surveil into a scry."""
    scry = build("Scry", amount=2)
    surveil = build("Surveil", amount=2)
    assert scry[0].kind is EffectKind.SCRY
    assert surveil[0].kind is EffectKind.SURVEIL
    assert scry[0].amount == surveil[0].amount


def test_amass_creates_an_army_only_when_you_have_none():
    """CR 701.44: put counters on an Army you control, creating one first if
    you have none. Two effects, and the order between them is load-bearing."""
    effects = build("Amass", amount=3)
    assert len(effects) == 2
    assert effects[0].kind is EffectKind.CONDITIONAL
    assert effects[1].kind is EffectKind.ADD_COUNTERS
    assert effects[1].counter_type == "+1/+1"


def test_support_targets_other_creatures():
    """CR 701.32: "up to N *other* target creatures", one counter each - not N
    counters on one creature."""
    effects = build("Support", amount=3)
    assert effects[0].targets is not None
    assert effects[0].targets.other_than_source
    assert effects[0].targets.up_to
    assert effects[0].amount.constant == 1


def test_an_action_instance_lower_cases_its_key():
    assert ActionInstance("Investigate").key == "investigate"
