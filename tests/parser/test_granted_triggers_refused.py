"""Granted triggered abilities: read, except the keywords that do not yet
work when granted.

The engine collects layered abilities now (CR 613.1f, 603.2), so a granted
trigger fires; the auditing session checked a granted quoted dies trigger,
exalted, ward and prowess on boards. Granted persist and undying return the
card every time, ignoring their counter condition (F22), and afterlife and
modular have not been checked granted, so abilities granting those are
refused.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card

REASON = "grants a triggered keyword that does not yet work when granted"


def _reasons(parsed):
    return {f.reason for face in parsed.faces for f in face.failures}


@pytest.mark.parametrize(
    "name", ["Abnormal Endurance", "Sublime Archangel"]
)
def test_a_working_granted_trigger_is_read(card_db, name):
    parsed = parse_card(card_db.lookup(name))
    assert REASON not in _reasons(parsed)
    assert parsed.fully_parsed


@pytest.mark.parametrize(
    "name",
    ["Mikaeus, the Unhallowed", "Undying Evil", "Afterlife Insurance",
     "Blaster, Combat DJ // Blaster, Morale Booster"],
)
def test_granted_persist_undying_afterlife_modular_are_refused(card_db, name):
    """Granted undying/persist loop (F22); afterlife and modular unchecked."""
    assert REASON in _reasons(parse_card(card_db.lookup(name)))


def test_a_granted_static_keyword_is_still_read(card_db):
    assert parse_card(card_db.lookup("Levitation")).fully_parsed
