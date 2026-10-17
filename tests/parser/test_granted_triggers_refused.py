"""An ability granting a triggered ability is refused while the engine cannot
fire granted triggers.

The trigger scan reads an object's printed abilities only, so Abnormal
Endurance's granted "When this creature dies, return it" and Sublime
Archangel's granted exalted never triggered: the cards looked read and did
nothing. Until the scan reads layered abilities (CR 613.1f, 603.2), these
cards report as not understood.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card

REASON = "grants a triggered ability the engine never collects"


@pytest.mark.parametrize(
    "name",
    [
        "Abnormal Endurance",  # quoted: has "When this creature dies, ..."
        "Sublime Archangel",  # keyword: other creatures you control have exalted
    ],
)
def test_a_granted_trigger_is_refused(card_db, name):
    parsed = parse_card(card_db.lookup(name))
    assert not parsed.fully_parsed
    reasons = {f.reason for face in parsed.faces for f in face.failures}
    assert REASON in reasons


def test_a_granted_static_keyword_is_still_read(card_db):
    """Flying is no trigger: granting it is read as before."""
    parsed = parse_card(card_db.lookup("Levitation"))
    assert parsed.fully_parsed
