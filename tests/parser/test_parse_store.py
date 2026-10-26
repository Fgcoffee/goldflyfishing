"""The stored parse is a cache of the parser and nothing else.

Served only while the code, the card data and the card itself match what it
was built from; otherwise every card is parsed live.
"""

from __future__ import annotations

import sqlite3

import pytest

from mtgfish.parser import store as parse_store
from mtgfish.parser.compile import OracleAbilities, parse_face

NAMES = ("Lightning Bolt", "Serra Angel", "Wort, the Raidmother", "Fire // Ice", "Turn to Frog")


@pytest.fixture
def built(card_db, tmp_path):
    cards = [card_db.lookup(name) for name in NAMES]
    path = tmp_path / "store.sqlite"
    parse_store.build(card_db, path=path, jobs=1, oracle_ids=[c.oracle_id for c in cards])
    return cards, path


def test_the_store_serves_exactly_the_live_parse(card_db, built):
    cards, path = built
    store = parse_store.ParseStore.open(card_db, path)
    assert store.fresh
    for card in cards:
        for face in range(max(1, len(card.faces))):
            abilities, failures = store.get(card, face)
            live = parse_face(card, face)
            assert repr(abilities) == repr(tuple(live.abilities)), card.name
            assert [f.reason for f in failures] == [f.reason for f in live.failures]


def test_oracle_abilities_reads_the_store(card_db, built):
    cards, path = built
    provider = OracleAbilities(store=parse_store.ParseStore.open(card_db, path))
    for card in cards:
        provider.abilities_for(card, 0)
    assert provider.served_from_store == len(cards)
    assert provider.parsed_live == 0


def _set_meta(path, key, value):
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("key", ["code", "data"])
def test_a_store_built_from_other_code_or_data_is_not_used(card_db, built, key):
    cards, path = built
    _set_meta(path, key, "something else")
    store = parse_store.ParseStore.open(card_db, path)
    assert not store.fresh
    assert store.get(cards[0], 0) is None
    provider = OracleAbilities(store=store)
    provider.abilities_for(cards[0], 0)
    assert provider.parsed_live == 1


def test_a_card_whose_text_changed_is_parsed_live(card_db, built):
    from dataclasses import replace

    cards, path = built
    store = parse_store.ParseStore.open(card_db, path)
    bolt = cards[0]
    reprinted = replace(bolt, faces=(replace(bolt.faces[0], oracle_text="Draw a card."),))
    assert store.get(reprinted, 0) is None


def test_the_code_fingerprint_covers_the_parser_and_the_rules():
    paths = [p for root in parse_store._CODE_ROOTS for p in (parse_store._PACKAGE / root).rglob("*.py")]
    names = {p.name for p in paths}
    assert {"clauses.py", "compile.py", "cr702_keyword_impl.py", "resolve.py"} <= names
