"""Plain-text decklist parsing.

Every deck site exports a slightly different flavour of the same idea, and
Moxfield's is the one that matters most because their API is closed to us. The
formats this needs to swallow:

    1 Sol Ring                         bare
    1x Sol Ring                        Archidekt
    Sol Ring                           no quantity
    1 Sol Ring (LTC) 302               Moxfield / Arena
    1 Sol Ring (LTC) 302 *F*           Arena foil marker
    1x Sol Ring (m3c) 236 [Ramp]       Archidekt with categories
    SB: 1 Sol Ring                     old-style sideboard prefix

plus section headers (``Commander``, ``Deck``, ``Sideboard``, ``// Commander``)
in any capitalisation, with or without a trailing count.

Nothing is ever silently dropped. A line that does not resolve to a card is
collected in ``Deck.unresolved`` and reported, because a quietly shorter deck
would change every statistic downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..cards import CardDef
from ..db import CardDatabase
from .model import Deck, DeckEntry, DeckIssue, Severity, validate

#: Section header word -> which pile it fills. Anything mapped to "ignore" is
#: parsed and discarded, so a maybeboard does not inflate the deck. A sideboard
#: and a companion are kept, but outside the deck: both start the game outside
#: it (CR 400.11a, 103.2b).
_SECTIONS = {
    "commander": "commander",
    "commanders": "commander",
    "command zone": "commander",
    "deck": "main",
    "main": "main",
    "maindeck": "main",
    "mainboard": "main",
    "creatures": "main",
    "lands": "main",
    "spells": "main",
    "sideboard": "sideboard",
    "sb": "sideboard",
    "maybeboard": "ignore",
    "considering": "ignore",
    "tokens": "ignore",
    "token": "ignore",
    # CR 103.2b: the card revealed as a companion, kept outside the game.
    "companion": "companion",
}

_COMMENT_PREFIXES = ("//", "#", ";")

_LINE_RE = re.compile(r"^\s*(?:(?P<qty>\d+)\s*[xX]?\s+)?(?P<name>.+?)\s*$")

#: Trailing decorations stripped from a card name, applied repeatedly until the
#: name stops changing. No real card name ends in a parenthesis or bracket, so
#: this cannot eat a legitimate name.
_TRAILING = (
    re.compile(r"\s*\*[A-Za-z]\*$"),  # *F* / *E* foil and etched markers
    re.compile(r"\s*\[[^\]]*\]$"),  # Archidekt [Category]
    re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9★-]*$"),  # (LTC) 302
    re.compile(r"\s*#\d+$"),
    re.compile(r"\s*<[^>]*>$"),
)

_HEADER_RE = re.compile(r"^\s*(?P<word>[A-Za-z][A-Za-z ]*?)\s*(?:\(\d+\))?\s*:?\s*$")


@dataclass(frozen=True, slots=True)
class ParsedLine:
    quantity: int
    name: str
    section: str
    raw: str


def clean_card_name(name: str) -> str:
    """Strip set codes, collector numbers, categories, and foil markers."""
    previous = None
    while previous != name:
        previous = name
        for pattern in _TRAILING:
            name = pattern.sub("", name)
    return name.strip()


def _match_header(line: str) -> str | None:
    """Return the section a header line introduces, or None if it isn't one."""
    text = line.strip()
    for prefix in _COMMENT_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    match = _HEADER_RE.match(text)
    if not match:
        return None
    return _SECTIONS.get(match.group("word").strip().lower())


def parse_lines(text: str) -> list[ParsedLine]:
    """Split decklist text into quantified, sectioned entries."""
    section = "main"
    out: list[ParsedLine] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # "SB: 1 Sol Ring" - a per-line section marker.
        if line.lower().startswith("sb:"):
            line = line[3:].strip()
            line_section = "sideboard"
        else:
            line_section = None

        header = _match_header(line)
        if header is not None and line_section is None:
            section = header
            continue

        stripped = line
        for prefix in _COMMENT_PREFIXES:
            if stripped.startswith(prefix):
                stripped = ""
                break
        if not stripped:
            continue

        match = _LINE_RE.match(stripped)
        if not match:
            continue
        quantity = int(match.group("qty") or 1)
        name = clean_card_name(match.group("name"))
        if not name:
            continue
        out.append(ParsedLine(quantity, name, line_section or section, raw.strip()))

    return out


def _resolve(db: CardDatabase, name: str) -> CardDef | None:
    """Look up a name, retrying with the raw text if cleaning was too eager."""
    card = db.lookup(name)
    if card is not None:
        return card
    # A name that survived cleaning but still fails might have had something
    # legitimate stripped; try progressively less-cleaned forms.
    if "(" in name:
        return db.lookup(name.split("(")[0])
    return None


def parse_decklist(
    text: str,
    db: CardDatabase,
    *,
    name: str = "Pasted deck",
    source: str = "text",
) -> Deck:
    """Parse decklist text into a validated Deck."""
    lines = parse_lines(text)

    commanders: list[CardDef] = []
    companions: list[CardDef] = []
    sideboard: list[CardDef] = []
    entries: list[DeckEntry] = []
    unresolved: list[str] = []
    issues: list[DeckIssue] = []

    explicit_commander_section = any(line.section == "commander" for line in lines)

    for line in lines:
        if line.section == "ignore":
            continue
        card = _resolve(db, line.name)
        if card is None and line.section == "sideboard":
            # CR 903.11: a sideboard card can never enter a Commander game,
            # so one that does not resolve changes nothing and is only noted.
            issues.append(
                DeckIssue(
                    Severity.INFO,
                    "unresolved-sideboard",
                    f"Sideboard card {line.name!r} not found; ignored.",
                )
            )
            continue
        if card is None:
            unresolved.append(line.name)
            suggestions = db.suggest(line.name)
            if suggestions:
                issues.append(
                    DeckIssue(
                        Severity.INFO,
                        "suggestion",
                        f"{line.name!r} not found. Did you mean: {', '.join(suggestions)}?",
                    )
                )
            continue
        if line.section == "commander":
            commanders.extend([card] * line.quantity)
        elif line.section == "companion":
            companions.extend([card] * line.quantity)
        elif line.section == "sideboard":
            sideboard.extend([card] * line.quantity)
        else:
            entries.append(DeckEntry(card, line.quantity))

    if not explicit_commander_section:
        commanders, entries, inferred = _infer_commanders(entries)
        if inferred:
            issues.append(
                DeckIssue(
                    Severity.INFO,
                    "inferred-commander",
                    "No commander section found; inferred "
                    + " + ".join(c.name for c in commanders)
                    + " from the top of the list.",
                )
            )
        elif not commanders:
            issues.append(
                DeckIssue(
                    Severity.ERROR,
                    "no-commander",
                    "No commander section found and the first card is not a legal "
                    "commander. Add a 'Commander' header or put the commander first.",
                )
            )

    # CR 103.2b: no more than one companion may be revealed.
    if len(companions) > 1:
        issues.append(
            DeckIssue(
                Severity.ERROR,
                "too-many-companions",
                f"{len(companions)} companions listed; a player reveals at most "
                f"one (CR 103.2b). Using {companions[0].name}.",
            )
        )

    deck = Deck(
        name=name,
        commanders=tuple(commanders),
        entries=tuple(entries),
        source=source,
        unresolved=tuple(unresolved),
        companion=companions[0] if companions else None,
        sideboard=tuple(sideboard),
    )
    return deck.with_issues(issues + validate(deck))


def _infer_commanders(
    entries: list[DeckEntry],
) -> tuple[list[CardDef], list[DeckEntry], bool]:
    """Guess the commander from an unsectioned list.

    Both Archidekt and Moxfield export the commander first, so the first entry
    being a legal commander is a strong signal. A second one immediately after
    is taken too, so partner pairs survive. The guess is always reported to the
    user rather than made silently.
    """
    from .model import _pairing_is_legal

    if not entries or not entries[0].card.can_be_commander:
        return [], entries, False

    commanders = [entries[0].card]
    rest = entries[1:]

    if rest:
        candidate = rest[0].card
        pairs = _pairing_is_legal(commanders[0], candidate) or (
            commanders[0].choose_a_background and candidate.is_background
        )
        if rest[0].quantity == 1 and pairs:
            commanders.append(candidate)
            rest = rest[1:]

    # The commander moves to the command zone, so it leaves the library. A
    # listing of "2 Kenrith" leaves one behind, which the singleton check will
    # then flag - correctly.
    trimmed = list(rest)
    if entries[0].quantity > 1:
        trimmed.insert(0, DeckEntry(entries[0].card, entries[0].quantity - 1))
    return commanders, trimmed, True
