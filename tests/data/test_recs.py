"""Recommendations: the parts that need no network.

EDHREC and Scryfall's search are live services and are not called here; the
tag fingerprint, the goal filters, the legality rules and the slug that names
an EDHREC page are all checked against the local snapshot.
"""

from __future__ import annotations

import datetime as dt

import pytest

from mtgfish.data import recs


@pytest.fixture
def index(card_db):
    return recs.tag_index(card_db)


@pytest.fixture
def offline(monkeypatch):
    """Both live sources down, as on a machine with no network."""

    def down(*args, **kwargs):
        raise recs.SourceError("offline")

    monkeypatch.setattr(recs, "fetch_edhrec", down)
    monkeypatch.setattr(recs, "recent_cards", down)


@pytest.mark.parametrize(
    "name, slug",
    [
        ("Atraxa, Praetors' Voice", "atraxa-praetors-voice"),
        ("Vito, Thorn of the Dusk Rose", "vito-thorn-of-the-dusk-rose"),
        ("Esika, God of the Tree // The Prismatic Bridge", "esika-god-of-the-tree"),
    ],
)
def test_edhrec_slug(name, slug):
    assert recs.edhrec_slug(name) == slug


def test_goals_match_their_tags():
    assert recs.goal_matches("ramp", {"mana rock"})
    assert recs.goal_matches("removal", {"removal-exile"})
    assert recs.goal_matches("tutors", {"tutor-to-hand"})
    # Searching for a land is not ramp unless the land arrives in play.
    assert not recs.goal_matches("ramp", {"tutor-land-basic", "tutor-to-hand"})
    assert recs.goal_matches(None, set())


def test_months_ago_crosses_the_year():
    assert recs.months_ago(6, dt.date(2026, 3, 31)) == dt.date(2025, 9, 28)
    assert recs.months_ago(1, dt.date(2026, 11, 9)) == dt.date(2026, 10, 9)


def test_noise_tags_are_left_out(index):
    sol_ring = index.cards[index.by_name["sol ring"]]
    assert "mana rock" in sol_ring.tags
    assert "activated ability" not in sol_ring.tags


def test_similar_cards_follow_the_deck(card_db, index, offline):
    rocks = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel", "Fellwar Stone",
             "Commander's Sphere", "Worn Powerstone", "Thran Dynamo"]
    found, notes = recs.recommend(
        card_db, deck_names=rocks, commanders=[], identity="WUBRG",
        mode="similar", limit=30,
    )
    assert found, notes
    assert not {rec.name for rec in found} & set(rocks)
    top = [index.cards[index.by_name[rec.name.casefold()]] for rec in found[:10]]
    assert sum("mana rock" in card.tags or "adds multiple mana" in card.tags for card in top) >= 7


def test_only_legal_cards_in_colour(card_db, index, offline):
    found, _ = recs.recommend(
        card_db, deck_names=["Sol Ring", "Dark Ritual", "Demonic Tutor"], commanders=[],
        identity="B", mode="similar", limit=200,
    )
    for rec in found:
        card = index.cards[index.by_name[rec.name.casefold()]]
        assert card.commander_legal, rec.name
        assert card.identity <= {"B"}, rec.name
        assert not card.is_land, rec.name


def test_goal_filter_narrows(card_db, index, offline):
    found, _ = recs.recommend(
        card_db, deck_names=["Sol Ring", "Swords to Plowshares", "Rhystic Study"],
        commanders=[], identity="WU", mode="similar", goal="removal", limit=40,
    )
    assert found
    for rec in found:
        assert recs.goal_matches("removal", index.cards[index.by_name[rec.name.casefold()]].tags)


def test_a_recency_filter_without_scryfall_says_so(card_db, offline):
    found, notes = recs.recommend(
        card_db, deck_names=["Sol Ring"], commanders=[], identity="C",
        mode="similar", months=6,
    )
    assert found == []
    assert any("recency" in note for note in notes)
