"""Deck import and Commander deck-construction validation (CR 903.5).

All offline: the Archidekt tests drive the payload parser with a synthetic
response rather than hitting the network, so the suite stays fast and does not
depend on a stranger's deck staying public.
"""

from __future__ import annotations

import pytest

from mtgfish.data.decks import load_deck, parse_decklist
from mtgfish.data.decks.archidekt import extract_deck_id, is_archidekt_url, parse_archidekt_payload
from mtgfish.data.decks.model import Severity
from mtgfish.data.decks.textlist import clean_card_name, parse_lines


def codes(deck) -> set[str]:
    return {issue.code for issue in deck.issues}


def errors(deck) -> list[str]:
    return [i.message for i in deck.issues if i.severity is Severity.ERROR]


# ---------------------------------------------------------------------------
# Line cleaning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sol Ring", "Sol Ring"),
        ("Sol Ring (LTC) 302", "Sol Ring"),
        ("Sol Ring (LTC) 302 *F*", "Sol Ring"),
        ("Sol Ring (m3c) 236 [Ramp]", "Sol Ring"),
        ("Sol Ring [Ramp{noPrice}]", "Sol Ring"),
        ("Sol Ring #123", "Sol Ring"),
        ("Fire // Ice (APC) 128", "Fire // Ice"),
        ("Urza's Mine (CHR) 114", "Urza's Mine"),
        # A collector number with a star suffix, as on promos.
        ("Sol Ring (PLST) 302★", "Sol Ring"),
    ],
)
def test_clean_card_name(raw, expected):
    assert clean_card_name(raw) == expected


@pytest.mark.parametrize(
    "line,qty,name",
    [
        ("1 Sol Ring", 1, "Sol Ring"),
        ("1x Sol Ring", 1, "Sol Ring"),
        ("4 Lightning Bolt", 4, "Lightning Bolt"),
        ("10x Forest", 10, "Forest"),
        ("Sol Ring", 1, "Sol Ring"),
    ],
)
def test_quantity_forms(line, qty, name):
    parsed = parse_lines(line)
    assert len(parsed) == 1
    assert parsed[0].quantity == qty
    assert parsed[0].name == name


def test_comments_and_blank_lines_ignored():
    assert parse_lines("\n# a comment\n\n; another\n1 Sol Ring\n") == parse_lines("1 Sol Ring")


def test_section_headers_route_cards():
    text = "Commander\n1 Kenrith, the Returned King\n\nDeck\n1 Sol Ring\n\nSideboard\n1 Black Lotus\n"
    sections = {line.name: line.section for line in parse_lines(text)}
    assert sections["Kenrith, the Returned King"] == "commander"
    assert sections["Sol Ring"] == "main"
    # CR 400.11a: a sideboard is kept, outside the deck.
    assert sections["Black Lotus"] == "sideboard"


def test_sb_prefix_marks_a_single_line():
    parsed = parse_lines("1 Sol Ring\nSB: 1 Black Lotus\n")
    assert [(p.name, p.section) for p in parsed] == [
        ("Sol Ring", "main"),
        ("Black Lotus", "sideboard"),
    ]


# ---------------------------------------------------------------------------
# Resolution and inference
# ---------------------------------------------------------------------------


def test_explicit_commander_section(card_db):
    deck = parse_decklist(
        "// Commander\n1 Kenrith, the Returned King\n// Deck\n1 Sol Ring\n", card_db
    )
    assert [c.name for c in deck.commanders] == ["Kenrith, the Returned King"]
    assert [e.card.name for e in deck.entries] == ["Sol Ring"]
    assert "inferred-commander" not in codes(deck)


def test_commander_inferred_from_first_line_and_reported(card_db):
    """Both Archidekt and Moxfield export the commander first.

    The guess is fine; making it silently would not be.
    """
    deck = parse_decklist("1 Kenrith, the Returned King\n1 Sol Ring\n", card_db)
    assert [c.name for c in deck.commanders] == ["Kenrith, the Returned King"]
    assert "inferred-commander" in codes(deck)


def test_partner_pair_inferred_together(card_db):
    deck = parse_decklist(
        "1 Thrasios, Triton Hero\n1 Tymna the Weaver\n1 Sol Ring\n", card_db
    )
    assert {c.name for c in deck.commanders} == {"Thrasios, Triton Hero", "Tymna the Weaver"}
    assert [e.card.name for e in deck.entries] == ["Sol Ring"]


def test_no_commander_is_an_error(card_db):
    deck = parse_decklist("1 Sol Ring\n1 Lightning Bolt\n", card_db)
    assert "no-commander" in codes(deck)


def test_unresolved_names_are_reported_not_dropped(card_db):
    deck = parse_decklist(
        "// Commander\n1 Kenrith, the Returned King\n// Deck\n1 Definitely Not A Card\n",
        card_db,
    )
    assert deck.unresolved == ("Definitely Not A Card",)
    assert "unresolved-card" in codes(deck)


def test_near_miss_gets_a_suggestion(card_db):
    deck = parse_decklist("// Commander\n1 Kenrith, the Returned King\n1 Sol Rng\n", card_db)
    suggestions = [i.message for i in deck.issues if i.code == "suggestion"]
    assert suggestions and "Sol Ring" in suggestions[0]


def test_curly_apostrophes_and_accents_resolve(card_db):
    deck = parse_decklist(
        "// Commander\n1 Atraxa, Praetors’ Voice\n// Deck\n1 Nazgul\n", card_db
    )
    assert deck.commanders[0].name == "Atraxa, Praetors' Voice"
    assert deck.unresolved == ()


# ---------------------------------------------------------------------------
# Construction rules (CR 903.5)
# ---------------------------------------------------------------------------


def _deck(card_db, commander: str, body: str):
    return parse_decklist(f"// Commander\n1 {commander}\n// Deck\n{body}", card_db)


def test_singleton_violation(card_db):
    deck = _deck(card_db, "Kenrith, the Returned King", "1 Sol Ring\n1 Sol Ring\n")
    assert "singleton" in codes(deck)


def test_basic_lands_are_exempt_from_singleton(card_db):
    deck = _deck(card_db, "Kenrith, the Returned King", "30 Forest\n")
    assert "singleton" not in codes(deck)


def test_relentless_rats_exempt(card_db):
    deck = _deck(card_db, "Kenrith, the Returned King", "20 Relentless Rats\n")
    assert "singleton" not in codes(deck)


def test_seven_dwarves_capped_at_seven(card_db):
    assert "singleton" not in codes(
        _deck(card_db, "Kenrith, the Returned King", "7 Seven Dwarves\n")
    )
    assert "singleton" in codes(
        _deck(card_db, "Kenrith, the Returned King", "8 Seven Dwarves\n")
    )


def test_color_identity_violation(card_db):
    """CR 903.5c: Yuriko is Dimir, so a green card is illegal."""
    deck = _deck(card_db, "Yuriko, the Tiger's Shadow", "1 Llanowar Elves\n")
    assert "color-identity" in codes(deck)


def test_color_identity_respects_mana_symbols_in_rules_text(card_db):
    """Kenrith is mono-white by cost but five-color by identity."""
    deck = _deck(card_db, "Kenrith, the Returned King", "1 Llanowar Elves\n")
    assert "color-identity" not in codes(deck)


def test_banned_card_is_rejected(card_db):
    deck = _deck(card_db, "Kenrith, the Returned King", "1 Black Lotus\n")
    assert "not-legal" in codes(deck)


def test_illegal_commander(card_db):
    deck = parse_decklist("// Commander\n1 Grizzly Bears\n// Deck\n1 Sol Ring\n", card_db)
    assert "illegal-commander" in codes(deck)


def test_illegal_pairing(card_db):
    """Two legendary creatures without any pairing ability cannot be paired."""
    deck = parse_decklist(
        "// Commander\n1 Kenrith, the Returned King\n1 Atraxa, Praetors' Voice\n"
        "// Deck\n1 Sol Ring\n",
        card_db,
    )
    assert "illegal-pairing" in codes(deck)


def test_legal_partner_pairing(card_db):
    deck = parse_decklist(
        "// Commander\n1 Thrasios, Triton Hero\n1 Tymna the Weaver\n// Deck\n1 Sol Ring\n",
        card_db,
    )
    assert "illegal-pairing" not in codes(deck)


def test_background_pairing(card_db):
    deck = parse_decklist(
        "// Commander\n1 Wilson, Refined Grizzly\n1 Raised by Giants\n// Deck\n1 Sol Ring\n",
        card_db,
    )
    assert "illegal-pairing" not in codes(deck)
    assert "illegal-commander" not in codes(deck)


def test_deck_size(card_db):
    """CR 903.5a counts the commander toward the 100."""
    deck = _deck(card_db, "Kenrith, the Returned King", "99 Forest\n")
    assert deck.total_cards == 100
    assert "deck-size" not in codes(deck)

    short = _deck(card_db, "Kenrith, the Returned King", "98 Forest\n")
    assert short.total_cards == 99
    assert "deck-size" in codes(short)


def test_commander_is_not_in_the_library(card_db):
    """CR 903.6: the commander starts in the command zone."""
    deck = _deck(card_db, "Kenrith, the Returned King", "99 Forest\n")
    assert deck.library_size == 99
    assert deck.total_cards == 100
    assert all(c.name != "Kenrith, the Returned King" for c in deck.library_cards())


# ---------------------------------------------------------------------------
# Archidekt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://archidekt.com/decks/2000000", "2000000"),
        ("https://archidekt.com/decks/2000000/yuriko-the-tigers-shadow", "2000000"),
        ("https://archidekt.com/api/decks/2000000/", "2000000"),
        ("2000000", "2000000"),
    ],
)
def test_deck_id_extraction(url, expected):
    assert extract_deck_id(url) == expected


def test_is_archidekt_url():
    assert is_archidekt_url("https://archidekt.com/decks/123/foo")
    assert not is_archidekt_url("https://moxfield.com/decks/abc")
    assert not is_archidekt_url("1 Sol Ring")


def _archidekt_card(name: str, oracle_id: str, quantity: int, categories):
    return {
        "quantity": quantity,
        "categories": categories,
        "card": {"oracleCard": {"name": name, "uid": oracle_id}},
    }


def test_archidekt_payload(card_db):
    kenrith = card_db.lookup("Kenrith, the Returned King")
    sol_ring = card_db.lookup("Sol Ring")
    lotus = card_db.lookup("Black Lotus")

    payload = {
        "name": "Test Deck",
        "categories": [{"name": "Cuts", "includedInDeck": False}],
        "cards": [
            _archidekt_card(kenrith.name, kenrith.oracle_id, 1, ["Commander"]),
            _archidekt_card(sol_ring.name, sol_ring.oracle_id, 1, ["Ramp"]),
            _archidekt_card(lotus.name, lotus.oracle_id, 1, ["Cuts"]),
        ],
    }
    deck = parse_archidekt_payload(payload, card_db)

    assert deck.name == "Test Deck"
    assert [c.name for c in deck.commanders] == ["Kenrith, the Returned King"]
    # The excluded category must not import, or the banned Lotus would appear.
    assert [e.card.name for e in deck.entries] == ["Sol Ring"]
    assert "not-legal" not in codes(deck)


def test_archidekt_maybeboard_excluded_by_name(card_db):
    lotus = card_db.lookup("Black Lotus")
    kenrith = card_db.lookup("Kenrith, the Returned King")
    payload = {
        "name": "d",
        "cards": [
            _archidekt_card(kenrith.name, kenrith.oracle_id, 1, ["Commander"]),
            _archidekt_card(lotus.name, lotus.oracle_id, 1, ["Maybeboard"]),
        ],
    }
    assert parse_archidekt_payload(payload, card_db).entries == ()


def test_archidekt_without_commander_category_errors(card_db):
    sol_ring = card_db.lookup("Sol Ring")
    payload = {"name": "d", "cards": [_archidekt_card(sol_ring.name, sol_ring.oracle_id, 1, [])]}
    assert "no-commander" in codes(parse_archidekt_payload(payload, card_db))


# ---------------------------------------------------------------------------
# Loader dispatch
# ---------------------------------------------------------------------------


def test_loader_reads_a_file(card_db, tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("// Commander\n1 Kenrith, the Returned King\n// Deck\n1 Sol Ring\n", "utf8")
    deck = load_deck(str(path), card_db)
    assert deck.commanders[0].name == "Kenrith, the Returned King"
    assert deck.name == "deck"


def test_loader_treats_plain_text_as_a_decklist(card_db):
    deck = load_deck("// Commander\n1 Kenrith, the Returned King\n1 Sol Ring\n", card_db)
    assert deck.commanders
