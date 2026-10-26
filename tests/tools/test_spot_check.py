"""The spot-check sheet: verdicts are read back from the reviewer's lines."""

from __future__ import annotations

from mtgfish.tools.spot_check import read_verdicts


def test_verdicts_are_read_with_their_notes(tmp_path):
    sheet = tmp_path / "sheet.md"
    sheet.write_text(
        "### 1. Lightning Bolt  (fully read)\n\n- `x`\n\nVERDICT: OK\n\n"
        "### 2. Phase Dolphin  (fully read)\n\nVERDICT: WRONG all attackers, not one\n\n"
        "### 3. Hubris  (NOT fully read)\n\nVERDICT: \n"
    )
    rows = read_verdicts(sheet)
    assert [(r["name"], r["read"], r["verdict"]) for r in rows] == [
        ("Lightning Bolt", True, "OK"),
        ("Phase Dolphin", True, "WRONG"),
        ("Hubris", False, ""),
    ]
    assert rows[1]["note"] == "all attackers, not one"
