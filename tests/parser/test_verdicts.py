"""Human verdicts, and the one thing they have to get right.

A verdict is only worth recording if it still means what it meant when it was
given. An approval that survives a grammar change silently vouches for
behaviour nobody looked at - which is worse than never having asked, because
it *looks* checked.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.parser.verdicts import Verdict, VerdictStore, fingerprint


@pytest.fixture
def store(tmp_path):
    return VerdictStore(tmp_path / "verdicts.json")


@pytest.fixture
def bolt(card_db):
    return card_db.lookup("Lightning Bolt")


def test_a_card_starts_unreviewed(store, bolt):
    verdict, current = store.status("Lightning Bolt", parse_card(bolt))
    assert verdict is Verdict.UNREVIEWED
    assert current


def test_an_approval_is_remembered(store, bolt):
    store.record("Lightning Bolt", Verdict.APPROVED, parse_card(bolt))
    verdict, current = store.status("Lightning Bolt", parse_card(bolt))
    assert verdict is Verdict.APPROVED
    assert current


def test_an_approval_lapses_when_the_parse_changes(store, bolt, card_db):
    """The property the whole design turns on.

    Approving Lightning Bolt and then having the grammar read it differently
    must not leave the card looking approved.
    """
    store.record("Lightning Bolt", Verdict.APPROVED, parse_card(bolt))

    different = parse_card(card_db.lookup("Giant Growth"))
    verdict, current = store.status("Lightning Bolt", different)
    assert verdict is Verdict.APPROVED
    assert not current, "a changed parse must invalidate the approval"


def test_marking_a_card_inert_suppresses_it(store, bolt):
    store.record("Lightning Bolt", Verdict.INERT, parse_card(bolt))
    assert store.is_suppressed("Lightning Bolt")


def test_a_stale_inert_verdict_still_suppresses(store, bolt, card_db):
    """Deliberately asymmetric with approval.

    Somebody judged this card's behaviour wrong. A grammar change might have
    fixed it, but assuming so without a second look is how a known-bad card
    walks back into a run.
    """
    store.record("Lightning Bolt", Verdict.INERT, parse_card(bolt))
    store.status("Lightning Bolt", parse_card(card_db.lookup("Giant Growth")))
    assert store.is_suppressed("Lightning Bolt")


def test_verdicts_survive_a_restart(tmp_path, bolt):
    path = tmp_path / "verdicts.json"
    VerdictStore(path).record("Lightning Bolt", Verdict.INERT, parse_card(bolt))
    assert VerdictStore(path).is_suppressed("Lightning Bolt")


def test_a_corrupt_file_does_not_stop_the_program(tmp_path):
    """Losing verdicts is recoverable; refusing to launch is not."""
    path = tmp_path / "verdicts.json"
    path.write_text("{not json at all", encoding="utf8")
    assert VerdictStore(path).counts()["approved"] == 0


def test_clearing_a_verdict_removes_it(store, bolt):
    store.record("Lightning Bolt", Verdict.INERT, parse_card(bolt))
    store.clear("Lightning Bolt")
    assert not store.is_suppressed("Lightning Bolt")


def test_the_fingerprint_tracks_the_reading_not_the_card_name(card_db):
    bolt = parse_card(card_db.lookup("Lightning Bolt"))
    assert fingerprint(bolt) == fingerprint(parse_card(card_db.lookup("Lightning Bolt")))
    assert fingerprint(bolt) != fingerprint(parse_card(card_db.lookup("Shock")))


# ---------------------------------------------------------------------------
# The part that actually matters: it changes the simulation
# ---------------------------------------------------------------------------


def test_an_inert_verdict_stops_the_engine_being_given_the_abilities(store, bolt):
    """Without this the review is cosmetic.

    Marking a card wrong has to stop the wrong behaviour, not just annotate
    it in a panel.
    """
    provider = OracleAbilities(verdicts=store)
    assert provider.abilities_for(bolt, 0), "should work before any verdict"

    store.record("Lightning Bolt", Verdict.INERT, parse_card(bolt))
    provider.clear()

    assert provider.abilities_for(bolt, 0) == ()
    assert "Lightning Bolt" in provider.suppressed


def test_an_approved_card_is_served_normally(store, bolt):
    store.record("Lightning Bolt", Verdict.APPROVED, parse_card(bolt))
    provider = OracleAbilities(verdicts=store)
    assert provider.abilities_for(bolt, 0)
    assert not provider.suppressed
