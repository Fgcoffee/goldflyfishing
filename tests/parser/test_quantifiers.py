"""How many: quantifiers on noun phrases, grouped by construct.

"another X", "any number of X", "up to N" - the count a noun phrase carries
decides whether an effect takes one object, a chosen number, or all of them,
and a missing count reads as "all".
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.nouns import parse_object_filter
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.query import Value


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _filter(text):
    stream = Stream.of(text)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done, f"{text!r} left {stream.remaining()}"
    return spec


def _effects(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert effects and stream.done, f"{text!r} left {stream.remaining()}"
    return effects


# -- "another X" is one X other than the source --------------------------------


@pytest.mark.parametrize(
    "text", ["another creature", "another creature you control", "another nonland permanent"]
)
def test_another_counts_one(text):
    spec = _filter(text)
    assert spec.other_than_source
    assert spec.count == Value.of(1)
    assert not spec.up_to


def test_another_target_leaves_the_count_to_targeting():
    assert _filter("another target creature").count == _filter("target creature").count


def test_sacrifice_another_creature_sacrifices_one():
    (effect,) = _effects("Sacrifice another creature.")
    assert effect.targets.count == Value.of(1)
    assert effect.targets.other_than_source


def test_a_condition_on_another_is_still_at_least_one(card_db):
    from mtgfish.parser import parse_card

    card = card_db.lookup("Bolg's Company")
    if card is None:
        pytest.skip("not in pool")
    assert parse_card(card).fully_parsed


# -- "any number of X" is a chosen count, not "all" (CR 107.1c) ----------------


@pytest.mark.parametrize(
    "text",
    [
        "any number of lands",
        "any number of target creatures",
        "any number of other target creatures you control",
        "any number of creature cards from your hand",
    ],
)
def test_any_number_of_is_up_to_with_no_ceiling(text):
    spec = _filter(text)
    assert spec.count is None
    assert spec.up_to
    assert spec.describe().startswith("any number of")


def test_all_is_still_all():
    spec = _filter("all lands")
    assert spec.count is None and not spec.up_to


# -- search: how many, where to, and nothing swallowed (CR 701.23) -------------


def _search(text):
    (effect,) = _effects(text)
    assert effect.kind is EffectKind.SEARCH_LIBRARY
    return effect


def test_search_up_to_two_finds_up_to_two():
    effect = _search(
        "Search your library for up to two basic land cards, put them onto the "
        "battlefield tapped, then shuffle."
    )
    assert effect.amount == Value.of(2)
    assert effect.zone is Zone.BATTLEFIELD and "tapped" in effect.keywords


def test_search_one_and_the_other_splits_the_cards():
    effect = _search(
        "Search your library for up to two basic land cards, reveal those cards, put one "
        "onto the battlefield tapped and the other into your hand, then shuffle."
    )
    assert effect.amount == Value.of(2)
    assert "other to hand" in effect.keywords


def test_tutor_to_top_is_the_library_after_the_shuffle():
    effect = _search("Search your library for a card, then shuffle and put that card on top.")
    assert effect.zone is Zone.LIBRARY


@pytest.mark.parametrize(
    "text",
    [
        # The discard is an effect of its own, and random discard is not read.
        "Search your library for a card, put that card into your hand, discard a card "
        "at random, then shuffle.",
        # Put on top and then shuffled is not on top.
        "Search your library for a card, put that card on top of your library, then shuffle.",
    ],
)
def test_search_riders_are_not_swallowed(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert not (effects and stream.done)


def test_search_any_number_of_has_no_fixed_count():
    effect = _search(
        "Search your library for any number of Elf creature cards, put them onto the "
        "battlefield, then shuffle."
    )
    assert effect.targets.count is None and effect.targets.up_to


# -- "where X is" reaches every X in the sentence (CR 107.3) --------------------


def _mentions_x(effects):
    from mtgfish.parser.clauses import _mentions_x

    return any(_mentions_x(effect) for effect in effects)


@pytest.mark.parametrize(
    "text",
    [
        # An operand of arithmetic: -X/-X is "0 minus X".
        "Each creature gets -X/-X until end of turn, where X is the number of cards in your hand.",
        # A bound inside a filter.
        "Destroy target creature with toughness X or less, where X is the number of lands you control.",
        # A token's power and toughness.
        "Create an X/X green Elemental creature token, where X is the number of lands you control.",
    ],
)
def test_where_x_is_reaches_every_x(text):
    effects = _effects(text)
    assert not _mentions_x(effects)
    assert all(effect.kind is not EffectKind.UNPARSED for effect in effects)


def test_an_x_mana_symbol_it_cannot_reach_is_refused():
    effects = _effects("Draw a card unless that player pays {X}, where X is this creature's power.")
    assert any(node.kind is EffectKind.UNPARSED for effect in effects for node in effect.walk())


def test_a_consumed_objects_mana_value_is_not_read_as_the_affected_objects():
    stream = Stream.of(
        "Search your library for a creature card with mana value X or less, where X is 2 "
        "plus the sacrificed creature's mana value."
    )
    effects = parse_effects(stream)
    assert not (effects and stream.done) or any(
        node.kind is EffectKind.UNPARSED for effect in effects for node in effect.walk()
    )


def test_a_number_plus_a_value_is_a_sum():
    from mtgfish.parser.nouns import parse_value
    from mtgfish.rules.kernel.query import ValueKind

    stream = Stream.of("2 plus the number of lands you control")
    value = parse_value(stream)
    assert stream.done and value.kind is ValueKind.SUM
