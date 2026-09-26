"""The Deck model and Commander deck-construction rules (CR 903.5).

Validation reports rather than raises. A deck that is one card short, or that
contains a card outside its commander's color identity, is still something the
user may want to goldfish - they should be told clearly and then allowed to
decide. Only things that make simulation impossible are hard errors.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Iterator

from ...rules.kernel.enums import Color, color_letters
from ..cards import CardDef


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass(frozen=True, slots=True)
class DeckIssue:
    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.name}] {self.message}"


@dataclass(frozen=True, slots=True)
class DeckEntry:
    card: CardDef
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class Deck:
    """A Commander deck: commanders in the command zone, everything else in the library."""

    name: str
    commanders: tuple[CardDef, ...] = ()
    entries: tuple[DeckEntry, ...] = ()
    #: CR 717.2: the supplementary Attraction deck, one CardDef per card. It
    #: is not part of the deck proper and counts toward no deck size.
    attractions: tuple[CardDef, ...] = ()
    source: str = ""
    #: Names that could not be resolved against the card database. Kept so the
    #: UI can show exactly what was dropped instead of silently shrinking the
    #: deck.
    unresolved: tuple[str, ...] = ()
    issues: tuple[DeckIssue, ...] = ()
    #: CR 103.2b / 702.139a: the card this deck reveals as its companion.
    #: It is not part of the deck - it starts the game outside it (CR 400.11)
    #: and so counts toward neither the 100 cards nor the singleton rule.
    companion: CardDef | None = None
    #: CR 400.11a: sideboard cards, also outside the game. CR 903.11 lets
    #: nothing bring them into a Commander game; they are kept so that
    #: outside the game holds what the list says it does.
    sideboard: tuple[CardDef, ...] = ()

    def as_decklist(self) -> str:
        """Back to the text form the paste box accepts.

        Round-tripping a fetched deck through text rather than passing the
        Deck object keeps one path into the simulator: whatever the user
        pastes and whatever a URL fetches are the same kind of thing by the
        time they reach it. Commanders are marked so the section survives.
        """
        lines = [f"{entry.quantity} {entry.card.name}" for entry in self.entries]
        if self.commanders:
            lines.append("")
            lines.append("// Commander")
            lines.extend(f"1 {card.name}" for card in self.commanders)
        if self.attractions:
            lines.append("")
            lines.append("// Attractions")
            lines.extend(f"1 {card.name}" for card in self.attractions)
        if self.companion is not None:
            lines.append("")
            lines.append("// Companion")
            lines.append(f"1 {self.companion.name}")
        if self.sideboard:
            lines.append("")
            lines.append("// Sideboard")
            lines.extend(f"1 {card.name}" for card in self.sideboard)
        return "\n".join(lines)

    # -- composition --------------------------------------------------------

    @property
    def library_size(self) -> int:
        return sum(e.quantity for e in self.entries)

    @property
    def total_cards(self) -> int:
        """Cards in the deck including commanders (CR 903.5a counts them)."""
        return self.library_size + len(self.commanders)

    def library_cards(self) -> list[CardDef]:
        """The starting library, one CardDef per physical card.

        Commanders are excluded: they begin in the command zone (CR 903.6).
        """
        out: list[CardDef] = []
        for entry in self.entries:
            out.extend([entry.card] * entry.quantity)
        return out

    def __iter__(self) -> Iterator[DeckEntry]:
        return iter(self.entries)

    # -- characteristics ----------------------------------------------------

    @property
    def color_identity(self) -> Color:
        """The deck's legal color identity: the union of its commanders' (CR 903.4)."""
        identity = Color.NONE
        for commander in self.commanders:
            identity |= commander.color_identity
        return identity

    @property
    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self.issues)

    def with_issues(self, issues: list[DeckIssue]) -> Deck:
        return replace(self, issues=tuple(issues))

    def __str__(self) -> str:
        commanders = " + ".join(c.name for c in self.commanders) or "(no commander)"
        return f"{self.name}: {commanders}, {self.total_cards} cards"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(deck: Deck) -> list[DeckIssue]:
    """Check a deck against the Commander construction rules (CR 903.5)."""
    issues: list[DeckIssue] = []

    for name in deck.unresolved:
        issues.append(
            DeckIssue(Severity.ERROR, "unresolved-card", f"Could not find card: {name!r}")
        )

    issues.extend(_check_commanders(deck))
    issues.extend(_check_size(deck))
    issues.extend(_check_singleton(deck))
    issues.extend(_check_color_identity(deck))
    issues.extend(_check_legality(deck))
    issues.extend(_check_attractions(deck))
    issues.extend(_check_companion(deck))
    return issues


def _check_commanders(deck: Deck) -> Iterator[DeckIssue]:
    if not deck.commanders:
        yield DeckIssue(
            Severity.ERROR, "no-commander", "Deck has no commander (CR 903.3)."
        )
        return

    if len(deck.commanders) > 2:
        yield DeckIssue(
            Severity.ERROR,
            "too-many-commanders",
            f"{len(deck.commanders)} commanders; at most two are ever legal.",
        )

    for commander in deck.commanders:
        if commander.is_background:
            continue  # Legal only as the second half of a pair, checked below.
        if not commander.can_be_commander:
            yield DeckIssue(
                Severity.ERROR,
                "illegal-commander",
                f"{commander.name} cannot be a commander (CR 903.3).",
            )

    if len(deck.commanders) == 2:
        first, second = deck.commanders
        if not _pairing_is_legal(first, second):
            yield DeckIssue(
                Severity.ERROR,
                "illegal-pairing",
                f"{first.name} and {second.name} cannot be paired as commanders "
                f"(CR 702.124). One of Partner, Partner with, Friends forever, "
                f"Doctor's companion, or Choose a Background is required.",
            )


def _pairing_is_legal(a: CardDef, b: CardDef) -> bool:
    """Whether two cards may be a commander pair (CR 702.124, 702.140, 702.164)."""
    # Plain Partner: any two cards with it.
    if a.partner and b.partner:
        return True
    # Partner with: only the specifically named pair.
    if a.partner_with == b.name or b.partner_with == a.name:
        return True
    if a.friends_forever and b.friends_forever:
        return True
    # Doctor's companion pairs with a Doctor (a Time Lord Doctor creature).
    if a.doctors_companion and _is_doctor(b):
        return True
    if b.doctors_companion and _is_doctor(a):
        return True
    # Choose a Background pairs with a Background enchantment.
    if a.choose_a_background and b.is_background:
        return True
    if b.choose_a_background and a.is_background:
        return True
    return False


def _is_doctor(card: CardDef) -> bool:
    line = card.front.type_line
    return line.has_subtype("Doctor") and line.has_subtype("Time Lord")


def _check_size(deck: Deck) -> Iterator[DeckIssue]:
    """CR 903.5a: exactly 100 cards, commanders included."""
    total = deck.total_cards
    if total != 100:
        yield DeckIssue(
            Severity.WARNING if 95 <= total <= 105 else Severity.ERROR,
            "deck-size",
            f"Deck has {total} cards; Commander requires exactly 100 (CR 903.5a).",
        )


def _check_singleton(deck: Deck) -> Iterator[DeckIssue]:
    """CR 903.5b: one copy of each card, by name."""
    counted: dict[str, int] = {}
    limits: dict[str, int] = {}
    for entry in deck.entries:
        counted[entry.card.name] = counted.get(entry.card.name, 0) + entry.quantity
        limits[entry.card.name] = entry.card.max_copies
    for commander in deck.commanders:
        counted[commander.name] = counted.get(commander.name, 0) + 1
        limits.setdefault(commander.name, commander.max_copies)

    for name, count in sorted(counted.items()):
        limit = limits[name]
        if count > limit:
            allowed = "any number" if limit >= 1_000_000 else str(limit)
            yield DeckIssue(
                Severity.ERROR,
                "singleton",
                f"{count} copies of {name}; only {allowed} allowed (CR 903.5b).",
            )


def _check_color_identity(deck: Deck) -> Iterator[DeckIssue]:
    """CR 903.5c: every card's color identity must fit within the commander's."""
    if not deck.commanders:
        return
    allowed = deck.color_identity
    offenders: list[str] = []
    for entry in deck.entries:
        if entry.card.color_identity & ~allowed:
            offenders.append(
                f"{entry.card.name} ({color_letters(entry.card.color_identity) or 'C'})"
            )
    if offenders:
        yield DeckIssue(
            Severity.ERROR,
            "color-identity",
            f"{len(offenders)} card(s) outside the commander's color identity "
            f"({color_letters(allowed) or 'colorless'}): {', '.join(offenders[:8])}"
            + (" ..." if len(offenders) > 8 else ""),
        )


def _check_legality(deck: Deck) -> Iterator[DeckIssue]:
    """Banned and non-legal cards. Scryfall marks bans as anything but 'legal'."""
    banned = [e.card.name for e in deck.entries if not e.card.commander_legal]
    banned += [c.name for c in deck.commanders if not c.commander_legal]
    banned += [c.name for c in deck.attractions if not c.commander_legal]
    if deck.companion is not None and not deck.companion.commander_legal:
        banned.append(deck.companion.name)
    if banned:
        yield DeckIssue(
            Severity.ERROR,
            "not-legal",
            f"Not legal in Commander: {', '.join(sorted(set(banned))[:8])}"
            + (" ..." if len(banned) > 8 else ""),
        )


#: CR 717.2a: the smallest Attraction deck constructed play allows.
MIN_ATTRACTION_DECK = 10


def _is_attraction(card: CardDef) -> bool:
    return card.front.type_line.has_subtype("Attraction")


def _check_attractions(deck: Deck) -> Iterator[DeckIssue]:
    """CR 717.2, 717.2a: the Attraction deck, and Attractions kept out of the
    deck proper."""
    # CR 717.2: an Attraction card does not begin the game in a player's deck.
    misplaced = sorted({e.card.name for e in deck.entries if _is_attraction(e.card)})
    if misplaced:
        yield DeckIssue(
            Severity.ERROR,
            "attraction-in-deck",
            f"Attractions belong in the Attraction deck, not the library "
            f"(CR 717.2): {', '.join(misplaced[:8])}",
        )
    if not deck.attractions:
        return
    strangers = sorted({c.name for c in deck.attractions if not _is_attraction(c)})
    if strangers:
        yield DeckIssue(
            Severity.ERROR,
            "not-an-attraction",
            f"Only Attraction cards go in an Attraction deck (CR 717.2): "
            f"{', '.join(strangers[:8])}",
        )
    # CR 717.2a: at least ten cards, each with a different English name.
    if len(deck.attractions) < MIN_ATTRACTION_DECK:
        yield DeckIssue(
            Severity.ERROR,
            "attraction-deck-size",
            f"Attraction deck has {len(deck.attractions)} cards; at least "
            f"{MIN_ATTRACTION_DECK} are required (CR 717.2a).",
        )
    counted: dict[str, int] = {}
    for card in deck.attractions:
        counted[card.name] = counted.get(card.name, 0) + 1
    repeated = sorted(name for name, count in counted.items() if count > 1)
    if repeated:
        yield DeckIssue(
            Severity.ERROR,
            "attraction-duplicate",
            f"Each card in an Attraction deck needs a different name "
            f"(CR 717.2a): {', '.join(repeated[:8])}",
        )


def _check_companion(deck: Deck) -> Iterator[DeckIssue]:
    """A companion is a card with companion that CR 903.11a lets in.

    CR 702.139a: only a card with a companion ability can be revealed as one.
    CR 903.11a: a card brought in from outside a Commander game may not share
    a name with a card in the starting deck, nor have a colour outside the
    commander's colour identity - checked here as well as in the game so the
    deck report says so before any game is played.

    The companion's own condition is card-specific text with no generic
    predicate behind it, so it is reported as unchecked rather than passed.
    """
    companion = deck.companion
    if companion is None:
        return
    if not any("Companion" in face.keywords for face in companion.faces):
        yield DeckIssue(
            Severity.ERROR,
            "not-a-companion",
            f"{companion.name} has no companion ability (CR 702.139a).",
        )
        return
    starting = {entry.card.name for entry in deck.entries}
    starting |= {card.name for card in deck.commanders}
    if companion.name in starting:
        yield DeckIssue(
            Severity.ERROR,
            "companion-name",
            f"{companion.name} is also in the deck, so it cannot be brought in "
            f"as a companion (CR 903.11a).",
        )
    if deck.commanders and companion.color_identity & ~deck.color_identity:
        yield DeckIssue(
            Severity.ERROR,
            "companion-color-identity",
            f"{companion.name} ({color_letters(companion.color_identity) or 'C'}) is "
            f"outside the commander's color identity (CR 903.11a).",
        )
    yield DeckIssue(
        Severity.INFO,
        "companion-condition-unchecked",
        f"{companion.name}'s companion condition is not verified against the "
        f"deck (CR 702.139a); the decklist is trusted.",
    )
