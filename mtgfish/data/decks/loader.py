"""One entry point for every way a deck can arrive.

The UI hands this whatever the user typed or pasted - a URL, a file path, or a
raw decklist - and gets back a validated Deck.
"""

from __future__ import annotations

from pathlib import Path

from ..db import CardDatabase
from .archidekt import fetch_archidekt, is_archidekt_url
from .model import Deck
from .textlist import parse_decklist


def load_deck(source: str, db: CardDatabase, *, name: str | None = None) -> Deck:
    """Load a deck from an Archidekt URL, a file path, or decklist text."""
    text = source.strip()

    if is_archidekt_url(text):
        deck = fetch_archidekt(text, db)
        return deck if name is None else _renamed(deck, name)

    # A single line that looks like a path, and exists. Checked before treating
    # the input as a decklist so a path is never parsed as a card name.
    if "\n" not in text and len(text) < 260:
        candidate = Path(text)
        try:
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8-sig", errors="replace")
                return parse_decklist(
                    content, db, name=name or candidate.stem, source=str(candidate)
                )
        except OSError:
            pass

    return parse_decklist(text, db, name=name or "Pasted deck", source="text")


def _renamed(deck: Deck, name: str) -> Deck:
    return Deck(
        name=name,
        commanders=deck.commanders,
        entries=deck.entries,
        source=deck.source,
        unresolved=deck.unresolved,
        issues=deck.issues,
    )
