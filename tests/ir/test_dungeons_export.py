"""The dungeons exported for the TS engine (mtgfish/ir/dungeons.py) are current."""

from __future__ import annotations

from pathlib import Path

from mtgfish.ir.dungeons import dungeons_json, dungeons_typescript

ROOT = Path(__file__).resolve().parents[2]


def test_committed_dungeon_files_are_current(card_db):
    """Regenerate with `python -m mtgfish.tools.ir_export dungeons`."""
    assert (ROOT / "schema" / "dungeons.json").read_text(encoding="utf-8") == dungeons_json(card_db)
    assert (ROOT / "engine" / "src" / "ir" / "dungeons.ts").read_text(encoding="utf-8") == dungeons_typescript(card_db)


def test_the_rules_dungeons_are_there(card_db):
    import json

    names = [d["name"] for d in json.loads(dungeons_json(card_db))["dungeons"]]
    assert "Lost Mine of Phandelver" in names
    assert "Undercity" in names
