"""Authored readings: hand-written IR for cards whose wording is one of a kind.

The parser reads a wording once and every card that shares it is covered,
this set's and the next. Some cards at the top of the play-rate list have a
wording no other card uses, and a grammar rule for each would be a rule for
one card. For those, a reading is written by hand, reviewed, and committed in
``mtgfish/ir/authored/<oracle_id>.json``:

    {
      "oracle_id": "...", "name": "...",
      "card_hash": "...",            # the card text it was written against
      "note": "why authored; anything a reviewer should know",
      "faces": [
        {"abilities": [
          {"text": "<the Oracle line>", "as": "<the paraphrase it was read from>",
           "ir": {<encoded Ability>}}
        ]}
      ]
    }

An ability whose ``ir`` is ``null`` (``as`` empty) is folded into another
line's paraphrase. ``as`` is the reviewer's check: a wording the parser already reads that
means what the Oracle line means. ``ir`` is what that paraphrase parsed to
(``python -m mtgfish.tools.authored``), kept so the reading doesn't move when
the grammar does; the readings lock still records it.

This is a deliberate, reviewed source, unlike the parse store (a cache).
The export (``mtgfish.ir.store.card_document``) serves it in place of the
parser's reading and marks the face ``authored``. A card whose text changed
since (``card_hash``) falls back to the parser, and ``tests/ir/
test_authored.py`` fails until it is re-authored or removed. Grammar rules
stay the first choice for any wording more than one card shares.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent / "authored"


@lru_cache(maxsize=None)
def _files() -> dict[str, Path]:
    if not DIRECTORY.is_dir():
        return {}
    return {path.stem: path for path in sorted(DIRECTORY.glob("*.json"))}


def entry(oracle_id: str) -> dict | None:
    """The authored file's contents for a card, or ``None``."""
    path = _files().get(oracle_id)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def abilities(card, digest: str) -> list[tuple] | None:
    """Each face's abilities, decoded, when the card has a current authored
    reading; ``None`` when it has none or its text has changed since."""
    from .codec import decode

    data = entry(card.oracle_id)
    if data is None or data.get("card_hash") != digest:
        return None
    return [
        tuple(decode(item["ir"], "Ability") for item in face["abilities"] if item["ir"] is not None)
        for face in data["faces"]
    ]


def digest() -> str:
    """A fingerprint of every authored file, for the export's code key."""
    import hashlib

    h = hashlib.sha256()
    for name, path in _files().items():
        h.update(name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


def oracle_ids() -> list[str]:
    return list(_files())
