"""Replicate on a targeted spell, and where warp may cast from."""

from __future__ import annotations

from mtgfish.parser import parse_card
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Zone


def _reasons(parsed):
    return {f.reason for face in parsed.faces for f in face.failures}


def test_replicate_on_a_targeted_spell_is_refused(card_db):
    """CR 702.56a: each copy may choose new targets; the engine's copies keep
    the original's, so Reiterating Bolt would hit one target every time."""
    parsed = parse_card(card_db.lookup("Reiterating Bolt"))
    assert not parsed.fully_parsed
    assert "replicate copies of a targeted spell cannot choose new targets" in _reasons(parsed)


def test_replicate_on_an_untargeted_spell_is_read(card_db):
    """Pulse of the Grid has no target to keep, so its copies are faithful."""
    parsed = parse_card(card_db.lookup("Pulse of the Grid"))
    assert "replicate copies of a targeted spell cannot choose new targets" not in _reasons(parsed)


def test_warp_casts_from_hand_only():
    """CR 702.185a: "cast this card from your hand" - not the command zone."""
    (ability,) = build(KeywordInstance("Warp"))
    assert ability.alternative_cost.from_zone is Zone.HAND
