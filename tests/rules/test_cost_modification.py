"""Cost reducers and taxers (CR 601.2f).

Two bugs stacked here, and the first is the worse kind:

* the grammar read the word "less" or "more" and then **threw it away** -
  ``sign`` was hardcoded positive, so every cost *reducer* in the format
  compiled as a cost *increase*. Urza's Incubator made creature spells cost
  two more. The card parsed, reported green, and did the opposite of what it
  says;

* and once the sign was right, nothing applied: the filter comes from a phrase
  like "artifact spells you cast", the word "spells" pins it to the stack, and
  the cost is worked out while the card is still in hand. The zone check
  rejected every candidate.

Both are invisible from the outside. A deck with a Foundry Inspector just
plays slightly worse than it should, which is exactly the error a goldfishing
tool exists to measure.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.casting import cost_increases, cost_reductions
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _net_cost(box, card_name, permanents):
    box.put(card_name, "hand", 0)
    for name in permanents:
        box.put(name, "battlefield", 0)
    box.game.invalidate_characteristics()

    spell = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None
        and obj.card.name == card_name
        and obj.zone.name == "HAND"
    )
    base = box.game.characteristics(spell).mana_cost.mana_value
    reduction = sum(cost_reductions(box.game, spell, spell.controller))
    increase = sum(cost_increases(box.game, spell, spell.controller))
    return max(0, base + increase - reduction)


def test_a_bare_board_changes_nothing(box):
    assert _net_cost(box, "Sol Ring", []) == 1


def test_a_reducer_makes_a_matching_spell_cheaper(box):
    assert _net_cost(box, "Sol Ring", ["Foundry Inspector"]) == 0


def test_two_reducers_both_apply(box):
    """CR 601.2f applies every applicable modification, floored at zero."""
    assert _net_cost(box, "Sol Ring", ["Foundry Inspector", "Etherium Sculptor"]) == 0


def test_a_taxer_makes_a_matching_spell_dearer(box):
    assert _net_cost(box, "Sol Ring", ["Thalia, Guardian of Thraben"]) == 2


def test_a_taxer_leaves_a_non_matching_spell_alone(box):
    """Thalia taxes noncreature spells; a Grizzly Bears is not one."""
    assert _net_cost(box, "Grizzly Bears", ["Thalia, Guardian of Thraben"]) == 2


def test_a_reducer_leaves_a_non_matching_spell_alone(box):
    """Foundry Inspector reduces artifact spells only."""
    assert _net_cost(box, "Grizzly Bears", ["Foundry Inspector"]) == 2


def test_a_reducer_and_a_taxer_compose(box):
    assert (
        _net_cost(box, "Sol Ring", ["Foundry Inspector", "Thalia, Guardian of Thraben"])
        == 1
    )


def test_the_reducer_ends_with_its_source(box):
    """CR 611.3 - derived from live permanents like every other static."""
    from mtgfish.rules.enums import Zone

    assert _net_cost(box, "Sol Ring", ["Foundry Inspector"]) == 0
    inspector = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Foundry Inspector"
    )
    box.game.move_object(inspector, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    spell = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Sol Ring"
    )
    assert sum(cost_reductions(box.game, spell, spell.controller)) == 0


# ---------------------------------------------------------------------------
# The sign, stated on its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_sign"),
    [
        ("Foundry Inspector", -1),
        ("Etherium Sculptor", -1),
        ("Urza's Incubator", -1),
        ("Thalia, Guardian of Thraben", 1),
    ],
)
def test_less_is_negative_and_more_is_positive(card_db, name, expected_sign):
    """The engine's convention is negative-is-cheaper, and both
    ``cost_reductions`` and ``cost_increases`` read it that way. A parser that
    ignores the word produces a card that does the opposite of what it says
    while still reporting as fully understood."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.effects import EffectKind

    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")

    amounts = [
        node.amount.constant
        for face in parse_card(card).faces
        for ability in face.abilities
        for effect in ability.effects
        for node in effect.walk()
        if node.kind is EffectKind.MODIFY_COST
    ]
    assert amounts, f"{name} produced no cost modification"
    assert all(
        (amount < 0) == (expected_sign < 0) for amount in amounts
    ), f"{name}: {amounts}"


@pytest.mark.parametrize(
    "name", ["Foundry Inspector", "Etherium Sculptor", "Urza's Incubator"]
)
def test_the_card_reads_completely(card_db, name):
    from mtgfish.parser import parse_card

    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")
    assert parse_card(card).fully_parsed
