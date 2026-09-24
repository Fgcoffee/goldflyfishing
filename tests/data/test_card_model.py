"""Card model against the real Scryfall pool.

These are the cards that break naive importers: shared face names, multi-word
subtypes, accented names, deck-limit exemptions, and the commander-eligibility
edge cases. Each assertion is checkable against a physical card.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.kernel.enums import CardType, Color, Layout, Supertype


def test_snapshot_is_sane(card_db):
    assert card_db.card_count > 30_000
    assert card_db.content_hash


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


def test_simple_lookup(card_db):
    card = card_db.lookup("Sol Ring")
    assert card.name == "Sol Ring"
    assert card.front.mana_cost.mana_value == 1
    assert card.front.type_line.has_type(CardType.ARTIFACT)


def test_lookup_is_case_and_space_insensitive(card_db):
    assert card_db.lookup("  sOl   riNG ").name == "Sol Ring"


def test_accented_names_resolve_without_the_accent(card_db):
    """Players type "Nazgul" and "Lim-Dul"; the cards are spelled otherwise."""
    assert card_db.lookup("Nazgul").name.startswith("Nazg")
    assert card_db.lookup("Lim-Dul the Necromancer").name.startswith("Lim-D")


def test_curly_apostrophes_resolve(card_db):
    """Word processors and Moxfield exports both produce these."""
    assert card_db.lookup("Urza’s Mine").name == "Urza's Mine"


def test_half_of_a_split_card_resolves_to_the_whole(card_db):
    assert card_db.lookup("Ice").name == "Fire // Ice"
    assert card_db.lookup("Fire").name == "Fire // Ice"


def test_back_face_name_resolves(card_db):
    card = card_db.lookup("Insectile Aberration")
    assert card.name == "Delver of Secrets // Insectile Aberration"


def test_art_series_never_wins_a_name_lookup(card_db):
    """Scryfall's art-series entry shares the face name and has no rules text.

    Resolving to it would silently put a blank card in the deck.
    """
    card = card_db.lookup("Delver of Secrets")
    assert card.layout == Layout.TRANSFORM
    assert card.front.type_line.has_type(CardType.CREATURE)


def test_unknown_name_returns_none_with_suggestions(card_db):
    assert card_db.lookup("Sol Rng") is None
    assert "Sol Ring" in card_db.suggest("Sol Rng")


# ---------------------------------------------------------------------------
# Faces and layouts
# ---------------------------------------------------------------------------


def test_split_card_offers_both_halves(card_db):
    """CR 709.4: either half of a split card may be cast."""
    card = card_db.lookup("Fire // Ice")
    assert len(card.castable_faces) == 2
    assert {f.name for f in card.castable_faces} == {"Fire", "Ice"}


def test_modal_dfc_offers_both_faces(card_db):
    """CR 712.2: either face of an MDFC may be played from hand."""
    card = card_db.lookup("Bala Ged Recovery")
    assert card.layout == Layout.MODAL_DFC
    assert len(card.castable_faces) == 2


def test_adventure_offers_both_halves(card_db):
    """CR 715.2: the Adventure or the creature."""
    card = card_db.lookup("Brazen Borrower")
    assert card.layout == Layout.ADVENTURE
    assert len(card.castable_faces) == 2


def test_transforming_card_only_casts_its_front(card_db):
    """CR 712.7: the back face is reached by transforming, never cast."""
    card = card_db.lookup("Delver of Secrets")
    assert len(card.faces) == 2
    assert len(card.castable_faces) == 1
    assert card.castable_faces[0].name == "Delver of Secrets"


def test_land_has_no_mana_cost(card_db):
    """CR 202.1: no mana cost is not the same as a cost of zero."""
    card = card_db.lookup("Dark Depths")
    assert not card.front.has_mana_cost
    assert card.front.type_line.has_supertype(Supertype.SNOW)
    assert card.front.type_line.has_supertype(Supertype.LEGENDARY)


def test_multiword_creature_type_survives_ingest(card_db):
    card = card_db.lookup("The Tenth Doctor")
    assert card.front.type_line.subtypes == ("Time Lord", "Doctor")


def test_land_subtypes_stay_separate(card_db):
    assert card_db.lookup("Urza's Mine").front.type_line.subtypes == ("Urza's", "Mine")


def test_phyrexian_cost_is_colored(card_db):
    card = card_db.lookup("Gitaxian Probe")
    assert card.front.mana_cost.colors == Color.BLUE
    assert card.front.mana_cost.mana_value == 1


# ---------------------------------------------------------------------------
# Deck construction rules (CR 903.5b)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Sol Ring", 1),
        ("Relentless Rats", 1_000_000),
        ("Seven Dwarves", 7),
        ("Nazgul", 9),
        ("Forest", 1_000_000),
    ],
)
def test_deck_limits(card_db, name, expected):
    assert card_db.lookup(name).max_copies == expected


def test_basic_land_detection(card_db):
    assert card_db.lookup("Snow-Covered Forest").is_basic_land
    assert not card_db.lookup("Bayou").is_basic_land


# ---------------------------------------------------------------------------
# Commander eligibility (CR 903.3)
# ---------------------------------------------------------------------------


def test_legendary_creature_can_be_commander(card_db):
    assert card_db.lookup("Kenrith, the Returned King").can_be_commander


def test_non_legendary_cannot(card_db):
    assert not card_db.lookup("Grizzly Bears").can_be_commander


def test_planeswalker_that_is_a_creature_elsewhere_can_be_commander(card_db):
    """Grist is a creature everywhere except the battlefield, so it qualifies."""
    grist = card_db.lookup("Grist, the Hunger Tide")
    assert grist.front.type_line.has_type(CardType.PLANESWALKER)
    assert grist.can_be_commander


def test_planeswalker_that_says_it_can_be_commander(card_db):
    assert card_db.lookup("Freyalise, Llanowar's Fury").can_be_commander


def test_background_is_not_a_commander_on_its_own(card_db):
    """A Background pairs with a Choose a Background commander (CR 702.124e)."""
    background = card_db.lookup("Raised by Giants")
    assert background.is_background
    assert not background.can_be_commander


def test_partner_detection(card_db):
    assert card_db.lookup("Thrasios, Triton Hero").partner
    assert card_db.lookup("Tymna the Weaver").partner
    assert not card_db.lookup("Kenrith, the Returned King").partner


def test_partner_with_names_its_pair(card_db):
    assert card_db.lookup("Pir, Imaginative Rascal").partner_with == "Toothy, Imaginary Friend"


def test_choose_a_background(card_db):
    assert card_db.lookup("Wilson, Refined Grizzly").choose_a_background


# ---------------------------------------------------------------------------
# Whole-pool sweep
# ---------------------------------------------------------------------------


def test_every_commander_legal_card_parses(card_db):
    """No Commander-legal card may fail to produce a usable definition.

    This is the regression net for new sets: a printing that breaks the model
    fails here rather than at simulation time.
    """
    failures = []
    typeless = []
    for card in card_db.iter_cards(commander_legal_only=True):
        try:
            face = card.front
            face.mana_cost.mana_value
            str(face.type_line)
            card.max_copies
            card.can_be_commander
            if not face.type_line.types:
                typeless.append(card.name)
        except Exception as exc:  # noqa: BLE001 - the point is to catch everything
            failures.append(f"{card.name}: {type(exc).__name__}: {exc}")

    assert not failures, f"{len(failures)} cards failed to parse: {failures[:10]}"
    assert not typeless, f"{len(typeless)} cards have no card type: {typeless[:10]}"


def test_no_unknown_type_words_on_legal_cards(card_db):
    """An unrecognised type word on a legal card means a missing CardType."""
    import json

    unknown = json.loads(card_db.meta("unknown_type_words", "{}") or "{}")
    assert not unknown, f"unknown type words: {unknown}"
