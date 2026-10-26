"""CR 714: Saga chapter lines.

A chapter symbol is the whole trigger condition (CR 714.2b); the text after
it is only the effect. Every chapter used to be read as a trigger condition
and failed.
"""

from __future__ import annotations

from mtgfish.parser import parse_face
from mtgfish.parser.explain import explain_ability
from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.events import EventKind


def _chapters(parsed):
    return [a for a in parsed.abilities if a.chapter]


def test_a_chapter_is_a_lore_counter_trigger_with_its_number(card_db):
    parsed = parse_face(card_db.lookup("The Eldest Reborn"), 0)
    assert parsed.fully_parsed, [str(f) for f in parsed.failures]
    chapters = _chapters(parsed)
    assert [a.chapter for a in chapters] == [1, 2, 3]
    for ability in chapters:
        assert ability.kind is AbilityKind.TRIGGERED
        assert ability.trigger.chapter == ability.chapter
        assert ability.trigger.event_kinds == frozenset({EventKind.COUNTER_ADDED})
        assert ability.trigger.counter_kind == "lore"
        assert ability.trigger.subject.source_only
    assert chapters[0].effects[0].kind is EffectKind.SACRIFICE
    assert chapters[1].effects[0].kind is EffectKind.DISCARD


def test_several_numerals_are_one_ability_each(card_db):
    """CR 714.2c: "I, II - [Effect]" is "I - [Effect]" and "II - [Effect]"."""
    parsed = parse_face(card_db.lookup("History of Benalia"), 0)
    assert parsed.fully_parsed
    chapters = _chapters(parsed)
    assert [a.chapter for a in chapters] == [1, 2, 3]
    assert chapters[0].effects == chapters[1].effects


def test_the_chapter_text_is_not_read_as_a_trigger(card_db):
    """"Whenever you cast a spell this turn" inside a chapter is a delayed
    trigger the chapter creates, never the chapter's own trigger - the
    chapter keeps its lore-counter trigger either way, or stays unread."""
    parsed = parse_face(card_db.lookup("Rediscover the Way"), 0)
    for ability in _chapters(parsed):
        assert ability.trigger.chapter == ability.chapter


def test_a_chapter_referring_back_to_another_stays_unread(card_db):
    """"III - Return the exiled card": the card chapter I exiled (CR 607).
    Read as "whatever this ability just acted on", it would return nothing."""
    parsed = parse_face(card_db.lookup("The Princess Takes Flight"), 0)
    assert not parsed.fully_parsed
    assert any("another chapter" in str(f) for f in parsed.failures)


def test_a_chapter_explains_as_a_chapter(card_db):
    parsed = parse_face(card_db.lookup("The Eldest Reborn"), 0)
    text = explain_ability(_chapters(parsed)[1])
    assert text.startswith("Chapter 2:")
    assert "lore" in text
