"""Replicate on a targeted spell, and where warp may cast from."""

from __future__ import annotations

from mtgfish.parser import parse_card
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import Zone


def test_replicate_on_a_targeted_spell_is_read(card_db):
    """CR 702.56a: each copy may choose new targets, and the engine's
    replicate copies carry that permission (CR 707.10c), so Reiterating Bolt
    is read again."""
    assert parse_card(card_db.lookup("Reiterating Bolt")).fully_parsed


def test_warp_casts_from_hand_only():
    """CR 702.185a: "cast this card from your hand" - not the command zone."""
    (ability,) = build(KeywordInstance("Warp"))
    assert ability.alternative_cost.from_zone is Zone.HAND


def _copy_keywords(card_db, name):
    parsed = parse_card(card_db.lookup(name))
    return parsed, [
        n.keywords
        for face in parsed.faces
        for a in face.abilities
        for e in a.effects
        for n in e.walk()
        if n.kind.name == "COPY_SPELL"
    ]


def test_the_copy_rider_is_carried_onto_the_copy(card_db):
    """CR 707.10c: Twincast's "You may choose new targets for the copy" is the
    copy's MAY_CHOOSE_NEW_TARGETS; the engine's copy keeps its targets
    without it."""
    parsed, keywords = _copy_keywords(card_db, "Twincast")
    assert parsed.fully_parsed
    assert keywords == [("may choose new targets",)]


def test_a_rider_after_another_instruction_is_refused(card_db):
    """Narset's Reversal: "Copy target instant or sorcery spell, then return it
    to its owner's hand. You may choose new targets for the copy." The rider
    is not carried onto the copy there, so the card is refused."""
    parsed = parse_card(card_db.lookup("Narset's Reversal"))
    assert not parsed.fully_parsed
    reasons = {f.reason for face in parsed.faces for f in face.failures}
    assert "a copy's 'choose new targets' is not carried onto the copy" in reasons
