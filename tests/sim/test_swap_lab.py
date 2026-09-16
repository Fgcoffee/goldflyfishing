"""The swap lab: what a plan expands to, and what a swap does to the deck.

The arithmetic is the whole feature and it is easy to get backwards, so it is
pinned here in the two shapes a person actually asks for:

* "ten different cards over ten others" - ten slots, one candidate each,
  applied together: one deck with ten changes.
* "five different cards in place of one specific card" - one slot, five
  candidates: five decks, because alternatives cannot combine.

And the third, added later: five *separate* swaps, each tried alone, which is
the only way to learn which of them is worth making.
"""

from __future__ import annotations

import pytest

from mtgfish.sim.lab import (
    MAX_VARIANTS,
    Slot,
    Swap,
    apply_swaps,
    comparison,
    deck_cards,
    plan,
)

DECK = "\n".join(
    [
        "1 Sol Ring",
        "1 Arcane Signet",
        "1 Command Tower",
        "1 Llanowar Elves",
        "60 Forest",
        "35 Grizzly Bears",
        "",
        "// Commander",
        "1 Omnath, Locus of Mana",
    ]
)


@pytest.fixture
def db(card_db):
    card_db.registry()
    return card_db


def _labels(variants):
    return [variant.label for variant in variants]


def test_the_commander_is_offered_for_swapping_too(db):
    """Swapping the commander is a bigger question, not an excluded one."""
    names = deck_cards(DECK, db)
    assert names[0] == "Omnath, Locus of Mana"
    assert "Sol Ring" in names


def test_one_slot_with_several_candidates_makes_one_deck_each(db):
    """Alternatives cannot combine: they all replace the same card."""
    variants, problems = plan(
        DECK,
        [Slot("Sol Ring", ("Mana Crypt", "Mana Vault", "Fellwar Stone"))],
        db,
    )
    assert not problems
    assert _labels(variants) == [
        "Baseline",
        "Sol Ring -> Mana Crypt",
        "Sol Ring -> Mana Vault",
        "Sol Ring -> Fellwar Stone",
    ]


def test_many_slots_combined_make_one_fully_swapped_deck(db):
    """"Ten different cards over ten others" is one deck, not ten."""
    slots = [
        Slot("Sol Ring", ("Mana Crypt",)),
        Slot("Arcane Signet", ("Fellwar Stone",)),
        Slot("Llanowar Elves", ("Birds of Paradise",)),
    ]
    variants, problems = plan(DECK, slots, db, combine=True)
    assert not problems
    assert len(variants) == 2, _labels(variants)
    assert len(variants[1].swaps) == 3


def test_many_slots_separately_make_one_deck_each(db):
    """The mode that can tell you *which* swap did the work."""
    slots = [
        Slot("Sol Ring", ("Mana Crypt",)),
        Slot("Arcane Signet", ("Fellwar Stone",)),
        Slot("Llanowar Elves", ("Birds of Paradise",)),
    ]
    variants, problems = plan(DECK, slots, db, combine=False)
    assert not problems
    assert _labels(variants) == [
        "Baseline",
        "Sol Ring -> Mana Crypt",
        "Arcane Signet -> Fellwar Stone",
        "Llanowar Elves -> Birds of Paradise",
    ]
    for variant in variants[1:]:
        assert len(variant.swaps) == 1


def test_a_combined_plan_that_would_explode_is_refused_rather_than_truncated(db):
    """Combined mode multiplies, and silently running some of it would lie."""
    slots = [
        Slot("Sol Ring", ("Mana Crypt", "Mana Vault", "Fellwar Stone")),
        Slot("Arcane Signet", ("Mind Stone", "Star Compass", "Coldsteel Heart")),
        Slot("Llanowar Elves", ("Birds of Paradise", "Elvish Mystic", "Fyndhorn Elves")),
        Slot("Command Tower", ("Path of Ancestry", "Exotic Orchard", "City of Brass")),
    ]
    variants, problems = plan(DECK, slots, db, combine=True)
    assert len(variants) == 1, "only the baseline should survive a refused plan"
    assert problems and str(MAX_VARIANTS) in problems[0]

    # The same slots read separately are twelve decks, not eighty-one.
    separate, trouble = plan(DECK, slots, db, combine=False)
    assert not trouble
    assert len(separate) == 13


def test_a_swap_keeps_the_deck_the_same_size(db):
    """A quantity is carried over, so the comparison is not against a
    differently sized deck - which would change the maths of every draw."""
    before = DECK
    after, problems = apply_swaps(before, (Swap("Forest", "Mountain"),), db)
    assert not problems
    assert "60 Mountain" in after
    assert "Forest" not in after


def test_the_commander_can_be_swapped(db):
    after, problems = apply_swaps(
        DECK, (Swap("Omnath, Locus of Mana", "Azusa, Lost but Seeking"),), db
    )
    assert not problems
    assert "Azusa, Lost but Seeking" in after
    assert "// Commander" in after


def test_an_unknown_card_is_reported_and_the_rest_still_run(db):
    """One bad candidate must not take the whole plan down with it."""
    after, problems = apply_swaps(
        DECK,
        (Swap("Sol Ring", "Definitely Not A Real Card"), Swap("Forest", "Island")),
        db,
    )
    assert any("Definitely Not A Real Card" in p for p in problems)
    assert "60 Island" in after, "the good swap still happened"


def test_a_card_that_is_not_in_the_deck_is_reported(db):
    _after, problems = apply_swaps(DECK, (Swap("Black Lotus", "Sol Ring"),), db)
    assert any("Black Lotus" in p for p in problems)


def test_a_candidate_equal_to_the_original_is_not_a_variant(db):
    """Trying a card against itself is a deck already in the table."""
    variants, _ = plan(DECK, [Slot("Sol Ring", ("Sol Ring", "Mana Crypt"))], db)
    assert _labels(variants) == ["Baseline", "Sol Ring -> Mana Crypt"]


def test_comparison_measures_every_variant_against_the_baseline(db):
    """The deltas are the point: an absolute win rate is a fact about the
    deck and the pod, while the delta is a fact about the card."""
    from mtgfish.sim.lab import LabResult, VariantResult
    from mtgfish.sim.stats import Report

    base = Report(games=100, wins=30)
    other = Report(games=100, wins=42)
    result = LabResult(
        variants=[
            VariantResult(label="Baseline", swaps=(), report=base),
            VariantResult(label="swap", swaps=("a -> b",), report=other),
        ],
        games=100,
    )
    rows = comparison(result)
    assert "delta_win_rate" not in rows[0], "the baseline has nothing to differ from"
    assert rows[1]["delta_win_rate"] == pytest.approx(0.12)
