"""Read the exported IR JSON: what a server needs to hand a deck to the browser.

    from mtgfish.ir.store import IRStore
    store = IRStore.open(db)                    # cache/ir/v2, written by `ir_export export`
    documents, missing = store.deck(["Lightning Bolt", "Sol Ring", "Mountain"])

Each document is the card's JSON (see docs/ir-json.md), ready to send as
is. A card whose file is missing, or was exported from different card text
(its ``card_hash`` no longer matches), is parsed and encoded live, so the
answer is never stale - only slower for those cards. Names that match no
card come back in ``missing``.

The whole export is stale when the code that wrote it differs from the code
running now: ``index.json`` records ``code_fingerprint()`` (the parser,
rules and card definitions, and this package's codec), and a store whose
export carries a different one parses every card live - and says so once in
the log - until ``ir_export export`` is run again.
"""

from __future__ import annotations

import json
from pathlib import Path

from .codec import IR_VERSION


import hashlib
import logging

_log = logging.getLogger(__name__)


_code: str | None = None


def code_fingerprint() -> str:
    """What the IR depends on besides the card data: the parser's own code
    fingerprint (parser, rules, card definitions) and the IR codec here.
    A change to either can change the JSON without changing a card.
    Computed once per process, like the parser's: a process runs the code it
    imported."""
    global _code
    if _code is not None:
        return _code
    from ..parser.store import code_fingerprint as parser_code

    digest = hashlib.sha256(parser_code().encode())
    package = Path(__file__).resolve().parent
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    _code = digest.hexdigest()
    return _code


def default_dir() -> Path:
    from ..paths import data_root

    return data_root() / "ir" / f"v{IR_VERSION}"


class IRStore:
    def __init__(self, db, directory: Path) -> None:
        self.db = db
        self.directory = directory
        self.live_parses = 0
        #: False when the export was written by other code: every card is
        #: then parsed live.
        self.current = self._export_is_current()

    def _export_is_current(self) -> bool:
        index = self.directory / "index.json"
        if not index.exists():
            return True  # nothing exported; each card is live anyway
        try:
            recorded = json.loads(index.read_text(encoding="utf-8")).get("code")
        except (OSError, ValueError):
            recorded = None
        if recorded == code_fingerprint():
            return True
        _log.warning(
            "IR export in %s was written by other parser code; serving live parses"
            " until `python -m mtgfish.tools.ir_export export` is run",
            self.directory,
        )
        return False

    @classmethod
    def open(cls, db, directory: Path | None = None) -> IRStore:
        return cls(db, directory or default_dir())

    def card(self, card) -> dict:
        """The card's IR document: the exported file if it is current, else live."""
        from ..parser.store import card_hash

        digest = card_hash(card)
        path = self.directory / f"{card.oracle_id}.json"
        if self.current and path.exists():
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
        from .codec import encode_card, encode_face

        faces = []
        for index in range(max(1, len(card.faces))):
            parsed = parse_face(card, index)
            faces.append(encode_face(card, index, parsed.abilities, parsed.failures, digest))
        return {
            "ir_version": IR_VERSION,
            "oracle_id": card.oracle_id,
            "name": card.name,
            "card_hash": digest,
            "card": encode_card(card),
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
