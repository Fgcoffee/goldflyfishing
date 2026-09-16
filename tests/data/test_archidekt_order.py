"""An Archidekt deck imports the same way however Archidekt orders it.

Archidekt returns a deck's cards in no stable order, and card order is the
library before the shuffle - so two fetches of one deck, with the same seed,
played different games. Runs from a link could not be reproduced.
"""

from __future__ import annotations

from mtgfish.data.decks.archidekt import parse_archidekt_payload


def _entry(name, quantity=1, categories=()):
    return {"quantity": quantity, "categories": list(categories), "card": {"oracleCard": {"name": name}}}


def _payload(cards):
    return {"name": "Order test", "categories": [], "cards": cards}


CARDS = [
    _entry("Atraxa, Praetors' Voice", categories=["Commander"]),
    _entry("Sol Ring"),
    _entry("Llanowar Elves"),
    _entry("Forest", 30),
    _entry("Counterspell"),
]


def test_card_order_from_archidekt_does_not_change_the_deck(card_db):
    forward = parse_archidekt_payload(_payload(CARDS), card_db)
    backward = parse_archidekt_payload(_payload(list(reversed(CARDS))), card_db)
    assert forward.as_decklist() == backward.as_decklist()
    assert [c.name for c in forward.commanders] == ["Atraxa, Praetors' Voice"]
