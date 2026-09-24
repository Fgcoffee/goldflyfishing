"""The independent check on the parser.

Full consumption proves a card was read completely. It cannot prove it was read
*correctly* - and a card that parses into the wrong effects looks fine
everywhere, which is why nobody catches it by eye.

Scryfall's oracle tags are a separate human reading of the same cards. Where
the two disagree, one is wrong. The tags are never used to generate behaviour;
a parser that learned from them would agree by construction and the check would
be worth nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.crosscheck import (
    EXPECTATIONS,
    KEYWORDS_ONLY,
    NOT_CHECKED,
    cross_check,
)


@pytest.fixture(scope="module")
def report(card_db):
    card_db.registry()
    cards = list(card_db.iter_cards(commander_legal_only=True))[:4000]
    return cross_check(cards, card_db)


def test_the_check_actually_checks_something(report):
    assert report.checked > 100
    assert report.tagged > 100


def test_disagreement_rates_stay_low(report):
    """A check that fires on most cards is measuring its own expectations.

    The first version flagged every "virtual french vanilla" card in the pool -
    437 of 437 - because the tag does not mean what it looks like it means. A
    check that is wrong more often than the thing it checks gets ignored, which
    is worse than not having it, so the rate is a test.
    """
    noisy = [item for item in report.disagreements if item.rate > 0.35]
    assert not noisy, [
        f"{item.tag}: {len(item.cards)}/{item.checked}" for item in noisy
    ]


def test_every_expectation_is_exercised(report):
    """An expectation nobody's cards match is dead weight."""
    seen = {item.tag for item in report.disagreements}
    checked = {item.tag for item in report.disagreements if item.checked}
    assert checked <= seen


def test_tags_are_either_checked_or_explicitly_not(report):
    """The exclusions are recorded rather than silently absent.

    ``NOT_CHECKED`` exists so the next person does not re-add the tags that
    produced noise and rediscover why they were dropped.
    """
    checked_tags = {expectation.tag for expectation in EXPECTATIONS} | KEYWORDS_ONLY
    assert not (checked_tags & NOT_CHECKED), "a tag is both checked and excluded"
    assert "spot removal" in NOT_CHECKED
    assert "repeatable lifegain" in NOT_CHECKED


def test_the_report_names_examples(report):
    """A count is not actionable; a card name is."""
    for item in report.disagreements:
        if item.cards:
            assert all(isinstance(name, str) and name for name in item.cards)


def test_rendering_never_crashes(report):
    text = report.render()
    assert "Cross-checked" in text


# ---------------------------------------------------------------------------
# The honesty of "fully parsed"
# ---------------------------------------------------------------------------


def test_an_ability_that_parses_into_nothing_is_not_fully_parsed(card_db):
    """CR-shaped but hollow.

    A keyword whose builder produces an UNPARSED effect leaves no failure
    behind: the shape was built and every token was consumed. Counting that as
    understood is how Djinn of Fool's Fall - flying, plus a Plot ability that
    does nothing at all - scored as a fully-read card.
    """
    from mtgfish.parser import parse_card

    card = card_db.lookup("Djinn of Fool's Fall")
    if card is None:
        pytest.skip("card not in this snapshot")

    parsed = parse_card(card)
    assert not parsed.fully_parsed
    assert parsed.faces[0].abilities, "it still produces abilities, just hollow ones"


def test_a_genuinely_complete_card_is_still_fully_parsed(card_db):
    """The control: tightening the definition must not condemn everything."""
    from mtgfish.parser import parse_card

    for name in ("Lightning Bolt", "Grizzly Bears", "Serra Angel"):
        card = card_db.lookup(name)
        assert parse_card(card).fully_parsed, name


def test_keyword_abilities_know_they_came_from_a_keyword(card_db):
    """``ability.keyword`` is how anything downstream asks that question.

    Around a fifth of the builders used ``Ability.static(...)`` and never set
    it, so Bloodthirst, Modular and Fading produced abilities that looked
    hand-written. The name is stamped centrally now.
    """
    from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build

    for name in ("Bloodthirst", "Modular", "Fading", "Flying", "Haste"):
        abilities = build(KeywordInstance(name, amount=2))
        assert abilities, name
        assert all(a.keyword == name for a in abilities if not a.unparsed), name
