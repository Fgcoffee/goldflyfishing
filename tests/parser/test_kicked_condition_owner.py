""""It was kicked" is the card's own payment only on a card with that cost.

Ertai's Trickery, "Counter target spell if it was kicked.", has no kicker:
"it" is the target spell, which the engine's payment condition cannot ask
about, so the ability is refused (auditor's F25). Goblin Bushwhacker's own
kicker reads as before.
"""

from __future__ import annotations

from mtgfish.parser import parse_card


def test_a_kicked_condition_about_another_spell_is_refused(card_db):
    parsed = parse_card(card_db.lookup("Ertai's Trickery"))
    assert not parsed.fully_parsed
    reasons = {f.reason for face in parsed.faces for f in face.failures}
    assert "'it was kicked' names another spell; this card has no such cost" in reasons


def test_a_card_with_its_own_kicker_is_read(card_db):
    assert parse_card(card_db.lookup("Goblin Bushwhacker")).fully_parsed
