"""Combat keywords read from real cards (CR 702).

The keyword builders are tested on a board in ``tests/rules``; these check
the parser hands each keyword line to its builder with the right parameter,
and that a keyword the engine does not implement still reports as unread.
"""

from __future__ import annotations

from mtgfish.parser import parse_face
from mtgfish.parser.split import LineKind, split_abilities
from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.events import EventKind


def _keyword_abilities(card_db, name: str, keyword: str):
    parsed = parse_face(card_db.lookup(name), 0)
    return [a for a in parsed.abilities if a.keyword.lower() == keyword.lower()]


# ---------------------------------------------------------------------------
# Keyword lines separated by semicolons
# ---------------------------------------------------------------------------


def test_semicolon_separated_keywords_are_several_abilities():
    """"Flying; flanking" is the same list as "Flying, flanking"."""
    lines = split_abilities("Flying; flanking")
    assert [line.text for line in lines] == ["Flying", "flanking"]
    assert all(line.kind is LineKind.KEYWORD for line in lines)


def test_a_protection_quality_does_not_swallow_the_next_keyword(card_db):
    """"Protection from black; flanking": the flanking is its own ability,
    not part of the quality protection is from."""
    assert _keyword_abilities(card_db, "Riftmarked Knight", "Flanking")
    assert _keyword_abilities(card_db, "Riftmarked Knight", "Protection")


# ---------------------------------------------------------------------------
# Bushido, rampage, flanking
# ---------------------------------------------------------------------------


def test_bushido_reads_its_amount(card_db):
    (ability,) = _keyword_abilities(card_db, "Konda, Lord of Eiganjo", "Bushido")
    assert ability.kind is AbilityKind.TRIGGERED
    assert ability.trigger.event_kinds == frozenset(
        {EventKind.BLOCKS, EventKind.BECOMES_BLOCKED}
    )
    (effect,) = ability.effects
    assert effect.kind is EffectKind.MODIFY_PT
    assert effect.amount.constant == 5 and effect.amount2.constant == 5


def test_rampage_scales_with_blockers_and_reads_its_amount(card_db):
    (ability,) = _keyword_abilities(card_db, "Craw Giant", "Rampage")
    assert ability.trigger.event_kinds == frozenset({EventKind.BECOMES_BLOCKED})
    (effect,) = ability.effects
    assert effect.kind is EffectKind.MODIFY_PT
    assert effect.amount.constant == 2  # the multiplier
    assert not effect.amount.is_constant


def test_flanking_triggers_once_per_blocker_and_shrinks_the_blocker(card_db):
    (ability,) = _keyword_abilities(card_db, "Benalish Cavalry", "Flanking")
    assert ability.trigger.event_kinds == frozenset({EventKind.BECOMES_BLOCKED_BY})
    assert ability.trigger.source.lacks_keyword == ("Flanking",)
    (effect,) = ability.effects
    assert effect.targets.trigger_source
    assert effect.amount.constant == -1 and effect.amount2.constant == -1


def test_an_unimplemented_combat_keyword_is_still_unread(card_db):
    """Owner's rule 1: a keyword with no faithful builder reports as unread."""
    (ability,) = _keyword_abilities(card_db, "Goblin Grappler", "Provoke")
    assert [e.kind for e in ability.effects] == [EffectKind.UNPARSED]
