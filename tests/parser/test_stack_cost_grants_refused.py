"""A cost granted to spells on the stack is refused, not read (auditor's F26).

Wort, the Raidmother gives red and green instants and sorceries conspire, but
the engine builds the optional-cost menu from the card before it is a spell,
so the grant was never offered. Read, it was a card that looked right and
did nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card


@pytest.mark.parametrize("name", ["Wort, the Raidmother", "Raiding Schemes"])
def test_conspire_grants_to_spells_are_refused(card_db, name):
    face = parse_card(card_db.lookup(name)).faces[0]
    grant = [a for a in face.abilities if "has conspire" in a.text]
    assert grant and all(a.unparsed for a in grant)
    assert any("granted to spells on the stack" in f.reason for f in face.failures)


def test_printed_conspire_is_still_read(card_db):
    assert parse_card(card_db.lookup("Burn Trail")).fully_parsed
