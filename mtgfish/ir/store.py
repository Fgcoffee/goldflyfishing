"""Read the exported IR JSON: what a server needs to hand a deck to the browser.

    from mtgfish.ir.store import IRStore
    store = IRStore.open(db)                    # cache/ir/v1, written by `ir_export export`
    documents, missing = store.deck(["Lightning Bolt", "Sol Ring", "Mountain"])

Each document is the card's JSON (see docs/ir-json.md), ready to send as
is. A card whose file is missing, or was exported from different card text
(its ``card_hash`` no longer matches), is parsed and encoded live, so the
answer is never stale - only slower for those cards. Names that match no
card come back in ``missing``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .codec import IR_VERSION


def default_dir() -> Path:
    from ..paths import data_root

    return data_root() / "ir" / f"v{IR_VERSION}"


class IRStore:
    def __init__(self, db, directory: Path) -> None:
        self.db = db
        self.directory = directory
        self.live_parses = 0

    @classmethod
    def open(cls, db, directory: Path | None = None) -> IRStore:
        return cls(db, directory or default_dir())

    def card(self, card) -> dict:
        """The card's IR document: the exported file if it is current, else live."""
        from ..parser.store import card_hash

        digest = card_hash(card)
        path = self.directory / f"{card.oracle_id}.json"
        if path.exists():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = None
            if (
                document is not None
                and document.get("ir_version") == IR_VERSION
                and document.get("card_hash") == digest
            ):
                return document
        self.live_parses += 1
        return self._live(card, digest)

    def _live(self, card, digest: str) -> dict:
        from ..parser.compile import parse_face
        from .codec import encode_face

        faces = []
        for index in range(max(1, len(card.faces))):
            parsed = parse_face(card, index)
            faces.append(encode_face(card, index, parsed.abilities, parsed.failures, digest))
        return {
            "ir_version": IR_VERSION,
            "oracle_id": card.oracle_id,
            "name": card.name,
            "card_hash": digest,
            "faces": faces,
        }

    def lookup(self, name: str):
        """The card a deck line names, by its full name or a face's name."""
        return self.db.lookup(name)

    def deck(self, names) -> tuple[list[dict], list[str]]:
        """One document per distinct card named, in first-seen order, and the
        names that matched nothing."""
        documents: list[dict] = []
        missing: list[str] = []
        seen: set[str] = set()
        for name in names:
            card = self.lookup(name)
            if card is None:
                missing.append(name)
                continue
            if card.oracle_id in seen:
                continue
            seen.add(card.oracle_id)
            documents.append(self.card(card))
        return documents, missing
