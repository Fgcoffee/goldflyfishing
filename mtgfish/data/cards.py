"""Card definitions: the immutable, printed characteristics of a card.

A ``CardDef`` is what is printed on a piece of cardboard. It is never mutated.
Everything that happens during a game - counters, damage, auras, control
changes - lives on a game *object* that references a CardDef, because a card
that changes zones becomes a new object with no memory of the old one
(CR 400.7).

The engine addresses *faces*, not cards. A modal double-faced card offers two
castable faces from hand; a transforming one offers a front face that later
becomes the back. Treating faces as the unit of characteristics keeps those
cases from becoming special cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..rules.enums import (
    BACK_FACE_ONLY_LAYOUTS,
    CardType,
    Color,
    DUAL_CASTABLE_LAYOUTS,
    LAYOUT_NAMES,
    Layout,
    Supertype,
    colors_from_letters,
)
from ..rules.mana import ManaCost
from ..rules.typeline import SubtypeRegistry, TypeLine

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

#: CR 903.5b: singleton, except basic lands and cards that grant themselves an
#: exemption. Relentless Rats says "any number"; Seven Dwarves says "up to
#: seven"; Nazgul says "up to nine".
_DECK_LIMIT_RE = re.compile(
    r"A deck can have (?:(any number) of|up to (\w+)) cards named", re.IGNORECASE
)

_PARTNER_WITH_RE = re.compile(r"^Partner with ([^(\n]+)", re.MULTILINE)

#: Grist, the Hunger Tide and its kin: a planeswalker that is a creature
#: everywhere except the battlefield, and so is a legal commander.
#:
#: The self-reference ("it's a ... creature") is load-bearing. Without it this
#: would also match a legendary artifact that animates something *else*, and
#: quietly make it a legal commander.
_CREATURE_ELSEWHERE_RE = re.compile(
    r"it(?:'s| is) a [^.]*\bcreature\b[^.]*in addition to its other types",
    re.IGNORECASE,
)

#: Unlimited for practical purposes; basics have no deck limit at all.
UNLIMITED = 1_000_000


@dataclass(frozen=True, slots=True)
class FaceDef:
    """One face of a card, with its printed characteristics (CR 205, 208)."""

    name: str
    mana_cost: ManaCost
    has_mana_cost: bool
    type_line: TypeLine
    oracle_text: str
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    defense: str | None = None
    colors: Color = Color.NONE
    color_indicator: Color | None = None
    keywords: tuple[str, ...] = ()
    produced_mana: tuple[str, ...] = ()

    @property
    def mana_value(self) -> int:
        """Mana value of this face's cost (CR 202.3)."""
        return self.mana_cost.mana_value

    @property
    def power_value(self) -> int | None:
        """Numeric power, or None for ``*``-style characteristic-defining values.

        A None here is a signal that the face needs a characteristic-defining
        ability computed in layer 7a (CR 613.4a), not that power is zero.
        """
        return _as_int(self.power)

    @property
    def toughness_value(self) -> int | None:
        return _as_int(self.toughness)

    @property
    def loyalty_value(self) -> int | None:
        return _as_int(self.loyalty)

    @property
    def defense_value(self) -> int | None:
        return _as_int(self.defense)

    @property
    def is_creature(self) -> bool:
        return self.type_line.has_type(CardType.CREATURE)

    @property
    def is_land(self) -> bool:
        return self.type_line.has_type(CardType.LAND)

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class CardDef:
    """A card as printed, shared by every copy of it in every deck."""

    oracle_id: str
    name: str
    layout: Layout
    faces: tuple[FaceDef, ...]
    mana_value: int
    color_identity: Color
    commander_legal: bool
    edhrec_rank: int | None = None
    scryfall_uri: str = ""

    # -- faces --------------------------------------------------------------

    @property
    def front(self) -> FaceDef:
        return self.faces[0]

    @property
    def castable_faces(self) -> tuple[FaceDef, ...]:
        """Faces that can be cast or played directly from hand.

        Split cards, modal double-faced cards, and Adventures offer a genuine
        choice at announcement (CR 601.2b, 709.4, 712.2, 715.2). Transforming
        and melding cards do not - their back face is only reached on the
        battlefield.
        """
        if self.layout in DUAL_CASTABLE_LAYOUTS:
            return self.faces
        if self.layout in BACK_FACE_ONLY_LAYOUTS:
            return self.faces[:1]
        return self.faces[:1]

    def face_named(self, name: str) -> FaceDef | None:
        for face in self.faces:
            if face.name == name:
                return face
        return None

    # -- deck construction --------------------------------------------------

    @property
    def is_basic_land(self) -> bool:
        return self.front.type_line.has_supertype(Supertype.BASIC) and self.front.is_land

    @property
    def max_copies(self) -> int:
        """Copies allowed in a Commander deck (CR 903.5b)."""
        if self.is_basic_land:
            return UNLIMITED
        for face in self.faces:
            match = _DECK_LIMIT_RE.search(face.oracle_text)
            if match:
                if match.group(1):
                    return UNLIMITED
                return _NUMBER_WORDS.get(match.group(2).lower(), UNLIMITED)
        return 1

    @property
    def is_legendary(self) -> bool:
        return self.front.type_line.has_supertype(Supertype.LEGENDARY)

    @property
    def can_be_commander(self) -> bool:
        """CR 903.3: a legendary creature, or a card that says it can be.

        Also covers the planeswalkers that are creatures everywhere except the
        battlefield, which are legal commanders even though their printed type
        line says otherwise.
        """
        if not self.commander_legal:
            return False
        front = self.front
        if self.is_legendary and front.is_creature:
            return True
        if any("can be your commander" in f.oracle_text.lower() for f in self.faces):
            return True
        if self.is_legendary and _CREATURE_ELSEWHERE_RE.search(front.oracle_text):
            return True
        return False

    @property
    def is_background(self) -> bool:
        return self.front.type_line.has_subtype("Background")

    @property
    def partner(self) -> bool:
        """Plain Partner (CR 702.124a): pairs with any other plain Partner."""
        for face in self.faces:
            for line in face.oracle_text.splitlines():
                stripped = line.strip()
                if stripped == "Partner" or stripped.startswith("Partner ("):
                    return True
        return False

    @property
    def partner_with(self) -> str | None:
        """The named partner, if this card has Partner with (CR 702.124b)."""
        for face in self.faces:
            match = _PARTNER_WITH_RE.search(face.oracle_text)
            if match:
                return match.group(1).strip().rstrip(".")
        return None

    @property
    def friends_forever(self) -> bool:
        return self._has_line_keyword("Friends forever")

    @property
    def doctors_companion(self) -> bool:
        return self._has_line_keyword("Doctor's companion")

    @property
    def choose_a_background(self) -> bool:
        return self._has_line_keyword("Choose a Background")

    def _has_line_keyword(self, keyword: str) -> bool:
        needle = keyword.lower()
        for face in self.faces:
            for line in face.oracle_text.splitlines():
                stripped = line.strip().lower()
                if stripped == needle or stripped.startswith(needle + " ("):
                    return True
        return False

    # -- construction from Scryfall ----------------------------------------

    @classmethod
    def from_scryfall(cls, data: dict, registry: SubtypeRegistry | None = None) -> CardDef:
        layout = LAYOUT_NAMES.get(data.get("layout", "normal"), Layout.NORMAL)
        faces = _faces_from_scryfall(data, registry)
        legalities = data.get("legalities", {})
        return cls(
            oracle_id=data.get("oracle_id") or data.get("id", ""),
            name=data.get("name", ""),
            layout=layout,
            faces=faces,
            mana_value=int(data.get("cmc") or 0),
            color_identity=colors_from_letters(data.get("color_identity", [])),
            commander_legal=legalities.get("commander") == "legal",
            edhrec_rank=data.get("edhrec_rank"),
            scryfall_uri=data.get("scryfall_uri", ""),
        )

    def __str__(self) -> str:
        return self.name


def _as_int(value: str | None) -> int | None:
    """Parse a printed numeric characteristic, or None if it isn't a plain number.

    Power and toughness can be ``*``, ``1+*``, ``?``, or negative. Only a plain
    integer is usable directly; anything else needs a characteristic-defining
    ability.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _faces_from_scryfall(data: dict, registry: SubtypeRegistry | None) -> tuple[FaceDef, ...]:
    """Build face definitions, inheriting card-level fields where a face omits them.

    Scryfall puts some characteristics only at the top level even for multi-face
    cards - a split card's colors, for instance - so each face falls back to the
    card-level value rather than ending up colorless.
    """
    raw_faces = data.get("card_faces")
    if not raw_faces:
        return (_one_face(data, data, registry),)
    return tuple(_one_face(face, data, registry) for face in raw_faces)


def _one_face(face: dict, card: dict, registry: SubtypeRegistry | None) -> FaceDef:
    type_line_text = face.get("type_line") or card.get("type_line") or ""
    # Art series and a few oddities carry a combined type line on the face;
    # take the half that belongs to this face.
    if "//" in type_line_text:
        type_line_text = type_line_text.split("//")[0].strip()

    mana_cost_text = face.get("mana_cost")
    if mana_cost_text is None:
        mana_cost_text = card.get("mana_cost")
    # An absent cost and an empty string mean different things upstream, but
    # both mean "no mana cost" here (CR 202.1). Lands and the back faces of
    # transforming cards genuinely have none.
    has_cost = bool(mana_cost_text)

    indicator = face.get("color_indicator")

    return FaceDef(
        name=face.get("name") or card.get("name", ""),
        mana_cost=ManaCost.parse(mana_cost_text),
        has_mana_cost=has_cost,
        # Tolerant here by design: ingest separately audits Commander-legal
        # cards for unknown type words and reports them loudly. A silver-border
        # card should not stop the database from building.
        type_line=(
            TypeLine.parse(type_line_text, registry, strict=False)
            if type_line_text
            else TypeLine()
        ),
        oracle_text=face.get("oracle_text") or card.get("oracle_text") or "",
        power=face.get("power", card.get("power")),
        toughness=face.get("toughness", card.get("toughness")),
        loyalty=face.get("loyalty", card.get("loyalty")),
        defense=face.get("defense", card.get("defense")),
        colors=colors_from_letters(face.get("colors", card.get("colors", []) or [])),
        color_indicator=colors_from_letters(indicator) if indicator else None,
        keywords=tuple(card.get("keywords", ())),
        produced_mana=tuple(face.get("produced_mana", card.get("produced_mana", ()) or ())),
    )
