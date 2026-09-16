"""The Deck tab's data: parse quality, images, curve and pips.

Headless, like the sandbox. What the tab shows in yellow and red is the whole
point of it, so which cards land in which state is tested against real cards.
"""

from __future__ import annotations

from mtgfish.data.decks import parse_decklist
from mtgfish.ui.deckview import card_row, deck_view, parse_status, summary

DECK = """\
1 Sol Ring
1 Llanowar Elves
2 Forest
1 Djinn of Fool's Fall
1 Frodo Nonexistent

// Commander
1 Atraxa, Praetors' Voice
"""


def test_a_fully_read_card_is_ok(card_db):
    assert parse_status(card_db.lookup("Sol Ring"))["status"] == "ok"


def test_a_card_with_nothing_to_read_is_not_a_gap(card_db):
    """A basic land prints no abilities; showing it red would be noise."""
    status = parse_status(card_db.lookup("Forest"))
    assert status["status"] == "ok"
    assert status["abilities"] == 0


def test_a_half_read_card_is_partial_not_blank(card_db):
    """Flying works and Plot does not: a card that still attacks and blocks."""
    status = parse_status(card_db.lookup("Djinn of Fool's Fall"))
    assert status["status"] == "partial"
    assert 0 < status["understood"] < status["abilities"]
    assert status["unread"]


def test_images_come_from_the_cdn_not_the_api(card_db):
    """A hundred API calls per deck would trip Scryfall's rate limit."""
    card = card_db.lookup("Sol Ring")
    row = card_row(card, card_db.raw_card(card.oracle_id), 1)
    assert row["images"]["normal"].startswith("https://cards.scryfall.io/normal/front/")
    assert row["images"]["back"] is None


def test_double_faced_cards_get_a_back_image(card_db):
    card = card_db.lookup("Delver of Secrets")
    row = card_row(card, card_db.raw_card(card.oracle_id), 1)
    assert "/back/" in row["images"]["back"]


def test_deck_view_reports_every_card_and_what_was_not_found(card_db):
    view = deck_view(parse_decklist(DECK, card_db), card_db)
    assert [c["name"] for c in view["commanders"]] == ["Atraxa, Praetors' Voice"]
    names = {c["name"]: c for c in view["cards"]}
    assert names["Forest"]["quantity"] == 2
    assert view["unresolved"] == ["Frodo Nonexistent"]
    assert view["color_identity"] == "WUBG"


def test_repeated_lines_become_one_tile(card_db):
    view = deck_view(parse_decklist("1 Forest\n1 Forest\n1 Sol Ring", card_db), card_db)
    forests = [c for c in view["cards"] if c["name"] == "Forest"]
    assert len(forests) == 1 and forests[0]["quantity"] == 2


def test_the_curve_leaves_lands_out_and_commanders_in(card_db):
    stats = deck_view(parse_decklist(DECK, card_db), card_db)["stats"]
    assert stats["lands"] == 2
    assert stats["curve"]["4"] == 1  # Atraxa
    assert sum(stats["curve"].values()) == stats["spells"] == 4
    assert stats["sources"]["G"] == 2


def _row(**overrides):
    row = {
        "quantity": 1, "type_group": "Creature", "status": "ok", "abilities": 1,
        "understood": 1, "is_land": False, "produced_mana": [], "mana_value": 2, "pips": {},
    }
    row.update(overrides)
    return row


def test_pips_count_each_copy():
    stats = summary([_row(quantity=3, pips={"R": 2})])
    assert stats["pips"] == {"R": 6}


def test_seven_and_up_share_a_bucket():
    stats = summary([_row(mana_value=7), _row(mana_value=11)])
    assert stats["curve"] == {"7": 2}


def test_type_ahead_prefers_names_that_start_with_the_text(card_db):
    names = card_db.search_names("lightning b")
    assert names[0].lower().startswith("lightning b")
    assert "Lightning Bolt" in names
    assert card_db.search_names("l") == []
