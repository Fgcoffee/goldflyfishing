"""Type lines: supertypes, card types, and subtypes (CR 205).

Subtypes are the awkward part. Most are a single word, so splitting on spaces
almost works - and then "Time Lord" arrives and a naive splitter silently
invents two creature types that do not exist. Meanwhile ``Urza's Mine`` really
does have two separate land types, ``Urza's`` and ``Mine``.

The only way to tell those apart is to know the actual set of subtypes, so this
module is registry-driven and does longest-match. The registry is refreshed
from Scryfall's catalog endpoints at ingest time, which means a new set's new
creature type needs no code change.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ..kernel.enums import (
    CARD_TYPE_NAMES,
    CARD_TYPE_ORDER,
    SUPERTYPE_DISPLAY_NAMES,
    SUPERTYPE_NAMES,
    SUPERTYPE_ORDER,
    TYPE_DISPLAY_NAMES,
    CardType,
    Supertype,
)

#: Scryfall renders the divider as an em dash surrounded by spaces. Older data
#: and hand-written test fixtures use other dashes, so accept them all.
_DASHES = ("—", "–", " - ")


class SubtypeRegistry:
    """Which subtypes exist, and which card type each belongs to.

    Answers two questions the rules constantly ask: "is Goblin a creature
    type?" (CR 205.3m, needed for changeling and every tribal effect) and "how
    do I split this subtype string into individual subtypes?".
    """

    __slots__ = ("_by_type", "_all", "_multiword", "_max_words")

    def __init__(self) -> None:
        self._by_type: dict[CardType, frozenset[str]] = {}
        self._all: set[str] = set()
        self._multiword: set[str] = set()
        self._max_words = 1

    def register(self, card_type: CardType, subtypes) -> None:
        names = frozenset(sys.intern(s) for s in subtypes)
        existing = self._by_type.get(card_type, frozenset())
        self._by_type[card_type] = existing | names
        for name in names:
            self._all.add(name)
            words = name.count(" ") + 1
            if words > 1:
                self._multiword.add(name)
                self._max_words = max(self._max_words, words)

    def types_for(self, subtype: str) -> CardType:
        """Every card type this subtype can appear on, as a flag."""
        result = CardType.NONE
        for card_type, names in self._by_type.items():
            if subtype in names:
                result |= card_type
        return result

    def is_creature_type(self, subtype: str) -> bool:
        return subtype in self._by_type.get(CardType.CREATURE, frozenset())

    def creature_types(self) -> frozenset[str]:
        """All creature types - what changeling grants (CR 702.73a)."""
        return self._by_type.get(CardType.CREATURE, frozenset())

    def known(self, subtype: str) -> bool:
        return subtype in self._all

    def split(self, text: str) -> tuple[str, ...]:
        """Split a subtype string into individual subtypes, longest match first.

        ``"Human Wizard"`` -> ``("Human", "Wizard")``
        ``"Time Lord Alien"`` -> ``("Time Lord", "Alien")``
        ``"Urza's Mine"`` -> ``("Urza's", "Mine")``

        Words that aren't in the registry are still emitted individually rather
        than dropped, so an unrecognised subtype from a brand-new set shows up
        in the type line instead of vanishing.
        """
        words = text.split()
        out: list[str] = []
        i = 0
        while i < len(words):
            matched = False
            # Try the longest candidate first so "Time Lord" beats "Time".
            upper = min(self._max_words, len(words) - i)
            for span in range(upper, 1, -1):
                candidate = " ".join(words[i : i + span])
                if candidate in self._multiword:
                    out.append(sys.intern(candidate))
                    i += span
                    matched = True
                    break
            if not matched:
                out.append(sys.intern(words[i]))
                i += 1
        return tuple(out)


def _default_registry() -> SubtypeRegistry:
    """A minimal registry so parsing is correct before any catalog is fetched.

    Only needs the multi-word subtypes: single-word ones split correctly with
    or without the registry. Replaced wholesale by the real catalogs at ingest.
    """
    reg = SubtypeRegistry()
    reg.register(CardType.CREATURE, ["Time Lord"])
    return reg


#: Used when no registry is supplied. Replaced by ``install_registry`` once the
#: card database has been built, so the whole process shares one snapshot.
DEFAULT_REGISTRY = _default_registry()


def install_registry(registry: SubtypeRegistry) -> None:
    global DEFAULT_REGISTRY
    DEFAULT_REGISTRY = registry


def active_registry() -> SubtypeRegistry:
    """The registry in force right now.

    A function rather than the module global, because ``install_registry``
    replaces that global after the card database loads - and any module that
    imported the name at import time would be holding the stub forever. The
    parser reads this on every subtype question for exactly that reason.
    """
    return DEFAULT_REGISTRY


class TypeLineError(ValueError):
    """A type line contained a word that is not a known type or supertype."""


@dataclass(frozen=True, slots=True)
class TypeLine:
    """A parsed type line (CR 205.1).

    Immutable. Type-changing effects in layer 4 (CR 613.1d) build a new
    TypeLine rather than mutating this one, so the printed values stay
    available.
    """

    supertypes: Supertype = Supertype.NONE
    types: CardType = CardType.NONE
    subtypes: tuple[str, ...] = ()

    @classmethod
    def scan(
        cls, text: str, registry: SubtypeRegistry | None = None
    ) -> tuple[TypeLine, tuple[str, ...]]:
        """Parse a type line, also returning any unrecognised type words.

        Separating the scan from the raise is what lets ingest be strict where
        it matters and tolerant where it does not: an unknown word on a
        Commander-legal card is a real engine gap and gets reported, while the
        Un-set card whose type line reads "Scariest Creature You'll Ever See"
        parses to what it can and is filtered out downstream anyway.
        """
        registry = registry or DEFAULT_REGISTRY
        text = text.strip()

        left, right = text, ""
        for dash in _DASHES:
            if dash in text:
                left, _, right = text.partition(dash)
                break

        supertypes = Supertype.NONE
        types = CardType.NONE
        unknown: list[str] = []
        for word in left.split():
            key = word.lower()
            if key in SUPERTYPE_NAMES:
                supertypes |= SUPERTYPE_NAMES[key]
            elif key in CARD_TYPE_NAMES:
                types |= CARD_TYPE_NAMES[key]
            else:
                unknown.append(word)

        subtypes = registry.split(right.strip()) if right.strip() else ()
        return cls(supertypes, types, subtypes), tuple(unknown)

    @classmethod
    def parse(
        cls, text: str, registry: SubtypeRegistry | None = None, *, strict: bool = True
    ) -> TypeLine:
        """Parse ``"Legendary Creature - Human Wizard"``.

        Strict by default: an unrecognised type word raises rather than being
        skipped, so a new card type cannot quietly produce an object with no
        types at all.
        """
        line, unknown = cls.scan(text, registry)
        if unknown and strict:
            raise TypeLineError(
                f"unknown type word(s) {', '.join(repr(w) for w in unknown)}"
                f" in type line {text!r}"
            )
        return line

    # -- queries ------------------------------------------------------------

    def has_type(self, card_type: CardType) -> bool:
        return bool(self.types & card_type)

    def has_supertype(self, supertype: Supertype) -> bool:
        return bool(self.supertypes & supertype)

    def has_subtype(self, subtype: str) -> bool:
        return subtype in self.subtypes

    @property
    def is_permanent_type(self) -> bool:
        from ..kernel.enums import PERMANENT_TYPES

        return bool(self.types & PERMANENT_TYPES)

    def creature_types(self, registry: SubtypeRegistry | None = None) -> tuple[str, ...]:
        """Subtypes that are creature types.

        A card's subtypes are only creature types if the card is a creature or
        Kindred (CR 205.3k), so this filters by the registry rather than
        assuming everything after the dash is tribal.
        """
        registry = registry or DEFAULT_REGISTRY
        return tuple(s for s in self.subtypes if registry.is_creature_type(s))

    # -- construction -------------------------------------------------------

    def with_types(self, types: CardType) -> TypeLine:
        return TypeLine(self.supertypes, types, self.subtypes)

    def adding(
        self,
        *,
        supertypes: Supertype = Supertype.NONE,
        types: CardType = CardType.NONE,
        subtypes: tuple[str, ...] = (),
    ) -> TypeLine:
        """Add types, as a layer-4 effect does (CR 613.1d).

        Adding a subtype never removes existing ones unless the effect says so
        (CR 205.1b), which is why this is additive by default.
        """
        merged = self.subtypes + tuple(s for s in subtypes if s not in self.subtypes)
        return TypeLine(self.supertypes | supertypes, self.types | types, merged)

    def removing(
        self,
        *,
        supertypes: Supertype = Supertype.NONE,
        types: CardType = CardType.NONE,
        subtypes: tuple[str, ...] = (),
    ) -> TypeLine:
        drop = set(subtypes)
        return TypeLine(
            self.supertypes & ~supertypes,
            self.types & ~types,
            tuple(s for s in self.subtypes if s not in drop),
        )

    def __str__(self) -> str:
        parts = [SUPERTYPE_DISPLAY_NAMES[s] for s in SUPERTYPE_ORDER if self.supertypes & s]
        parts += [TYPE_DISPLAY_NAMES[t] for t in CARD_TYPE_ORDER if self.types & t]
        head = " ".join(parts)
        if self.subtypes:
            return f"{head} — {' '.join(self.subtypes)}"
        return head
