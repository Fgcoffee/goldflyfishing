"""Devotion (CR 700.5).

The rule is counted per *symbol*, not per permanent, and that is the whole
trap: one Gray Merchant of Asphodel is worth two, not one. A hybrid symbol
counts once if either half matches (CR 700.5b), which is why the check asks a
symbol which colours can pay it rather than testing equality.

Nykthos is the card that makes this matter for a goldfishing simulator - it
turns a devotion count into mana, so getting the count wrong changes how much
the deck can do on a turn.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.query import Value, ValueKind
from mtgfish.rules.kernel.values import evaluate
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _devotion(box, colour):
    return evaluate(
        box.game,
        Value(kind=ValueKind.DEVOTION, colors=colour),
        controller=box.game.player(0).id,
    )


def test_an_empty_board_has_no_devotion(box):
    assert _devotion(box, Color.BLACK) == 0


def test_devotion_counts_symbols_not_permanents(box, card_db):
    """Gray Merchant costs {3}{B}{B}: one permanent, two black symbols."""
    if card_db.lookup("Gray Merchant of Asphodel") is None:
        pytest.skip("Gray Merchant not in this pool")
    box.put("Gray Merchant of Asphodel", "battlefield", 0)
    box.game.invalidate_characteristics()
    assert _devotion(box, Color.BLACK) == 2


def test_devotion_ignores_generic_mana(box):
    """Sol Ring costs {1}: no coloured symbols at all."""
    box.put("Sol Ring", "battlefield", 0)
    box.game.invalidate_characteristics()
    assert _devotion(box, Color.BLACK) == 0
    assert _devotion(box, Color.GREEN) == 0


def test_devotion_is_per_colour(box, card_db):
    if card_db.lookup("Gray Merchant of Asphodel") is None:
        pytest.skip("Gray Merchant not in this pool")
    box.put("Gray Merchant of Asphodel", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)  # {1}{G}
    box.game.invalidate_characteristics()

    assert _devotion(box, Color.BLACK) == 2
    assert _devotion(box, Color.GREEN) == 1
    assert _devotion(box, Color.BLUE) == 0


def test_devotion_to_two_colours_counts_either(box, card_db):
    """CR 700.5b: a symbol counts if it matches any of the named colours."""
    if card_db.lookup("Gray Merchant of Asphodel") is None:
        pytest.skip("Gray Merchant not in this pool")
    box.put("Gray Merchant of Asphodel", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    box.game.invalidate_characteristics()
    assert _devotion(box, Color.BLACK | Color.GREEN) == 3


def test_devotion_only_counts_your_permanents(box, card_db):
    if card_db.lookup("Gray Merchant of Asphodel") is None:
        pytest.skip("Gray Merchant not in this pool")
    box.put("Gray Merchant of Asphodel", "battlefield", 1)
    box.game.invalidate_characteristics()
    assert _devotion(box, Color.BLACK) == 0


# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "colours"),
    [
        ("your devotion to black", Color.BLACK),
        ("your devotion to blue and black", Color.BLUE | Color.BLACK),
    ],
)
def test_devotion_reads(card_db, text, colours):
    from mtgfish.parser.nouns import parse_value
    from mtgfish.parser.tokens import Stream

    card_db.registry()
    stream = Stream.of(text)
    value = parse_value(stream)
    assert value is not None and stream.done
    assert value.kind is ValueKind.DEVOTION
    assert value.colors == colours


def test_nykthos_reads(card_db):
    """"Add an amount of mana of that color equal to your devotion to that
    color" puts the quantity after the colour, the opposite way round from
    every other mana ability."""
    from mtgfish.parser import parse_card

    card = card_db.lookup("Nykthos, Shrine to Nyx")
    if card is None:
        pytest.skip("Nykthos not in this pool")
    card_db.registry()
    assert parse_card(card).fully_parsed
