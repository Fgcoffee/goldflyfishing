"""schema/fixtures/ (the engine's real-card test fixtures) is current."""

from __future__ import annotations

import json
from pathlib import Path

from mtgfish.ir.store import card_document
from mtgfish.tools.ir_export import fixture_names

ROOT = Path(__file__).resolve().parents[2]


def test_committed_fixtures_are_current(card_db, monkeypatch):
    """Regenerate with `python -m mtgfish.tools.ir_export fixtures`."""
    monkeypatch.setenv("MTGFISH_PARSE_STORE", "off")
    names = fixture_names(ROOT / "schema" / "fixtures.txt")
    wanted = {}
    for name in names:
        card = card_db.lookup(name)
        assert card is not None, name
        wanted[f"{card.oracle_id}.json"] = json.dumps(card_document(card), indent=1, ensure_ascii=False) + "\n"
    files = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "schema" / "fixtures").glob("*.json")}
    assert set(files) == set(wanted)
    for file, text in wanted.items():
        assert files[file] == text, file
