"""The census breakdown sorts every card into one state by how much was read."""

from __future__ import annotations

from mtgfish.tools.census import breakdown


def _ability(text, ok, unread=0, engine=False, remaining=""):
    row = {"text": text, "ok": ok}
    if not ok:
        words = len(text.split())
        row.update(words=words, unread=unread, engine=engine, remaining=remaining)
    return row


def _card(name, *abilities):
    ok = all(a["ok"] for a in abilities)
    return {"name": name, "status": "ok" if ok else "failed", "abilities": list(abilities)}


def test_each_card_lands_in_exactly_one_state():
    records = [
        _card("Read", _ability("Draw a card.", True)),
        _card(
            "Partial",
            _ability("Flying", True),
            _ability("Draw a card unless zorble.", False, unread=2, remaining="unless zorble"),
        ),
        _card("Nothing", _ability("Zorble the flimflam.", False, unread=4, remaining="Zorble")),
        _card("Engine", _ability("Its controller draws a card.", False, engine=True)),
    ]
    report = breakdown(records)
    assert "fully read                              1" in report
    assert "1-2 words unread                      1" in report
    assert "nothing read                            1" in report
    assert "read, but engine can't tie it           1" in report
    # "unless" is not used by any read ability here, and "zorble" never is.
    assert "1-2 unknown words                     1" in report
