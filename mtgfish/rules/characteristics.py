"""Characteristics of an object (CR 109.3).

An object's characteristics are name, mana cost, color, color indicator, card
type, subtype, supertype, rules text, abilities, power, toughness, loyalty,
defense, hand modifier, and life modifier. Nothing else - notably *not* whether
it is tapped, what damage it has marked, or what counters are on it. Those are
status and are tracked on the game object, because they survive things that
change characteristics and are changed by things that do not.

Characteristics are immutable and derived. Nothing writes a permanent's power;
the layer system (CR 613) computes it from printed values plus every applicable
continuous effect, and hands back a fresh instance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .abilities import Ability
from .cr106_mana import ZERO_COST, ManaCost
from .cr205_typeline import TypeLine
from .enums import CardType, Color, Supertype

#: Unwrapped once here rather than on every call - see the note on
#: ``has_type`` for why an IntFlag comparison is worth avoiding in a
#: function the engine calls a million times a game.
_CREATURE = int(CardType.CREATURE)
_LAND = int(CardType.LAND)
_LEGENDARY = int(Supertype.LEGENDARY)


@dataclass(frozen=True, slots=True)
class Characteristics:
    """The complete characteristic set of an object."""

    name: str = ""
    mana_cost: ManaCost = ZERO_COST
    #: CR 202.1: some objects have no mana cost at all, which is not the same
    #: as a cost of zero. Lands, and the back faces of transforming cards.
    has_mana_cost: bool = False
    #: CR 105.2. Normally derived from the mana cost, but a color indicator or
    #: a layer-5 effect can override it, so it is stored rather than computed.
    colors: Color = Color.NONE
    type_line: TypeLine = TypeLine()
    abilities: tuple[Ability, ...] = ()
    power: int | None = None
    toughness: int | None = None
    loyalty: int | None = None
    defense: int | None = None
    hand_modifier: int = 0
    life_modifier: int = 0
    text: str = ""

    # -- type queries -------------------------------------------------------

    @property
    def types(self) -> CardType:
        return self.type_line.types

    @property
    def subtypes(self) -> tuple[str, ...]:
        return self.type_line.subtypes

    @property
    def supertypes(self) -> Supertype:
        return self.type_line.supertypes

    # These five are the most-called functions in the engine - three million
    # times in a single game between them - and each was one IntFlag ``&``.
    #
    # ``IntFlag.__and__`` does not do what it looks like: it builds a *new
    # enum member* for the result, through ``_missing_``, ``__new__`` and a
    # value lookup. Comparing the underlying integers is the same test for a
    # tenth of the cost, and profiling put this at a fifth of the whole run.
    def has_type(self, card_type: CardType) -> bool:
        return bool(int(self.type_line.types) & int(card_type))

    def has_subtype(self, subtype: str) -> bool:
        return subtype in self.type_line.subtypes

    def has_supertype(self, supertype: Supertype) -> bool:
        return bool(int(self.type_line.supertypes) & int(supertype))

    @property
    def is_creature(self) -> bool:
        return bool(int(self.type_line.types) & _CREATURE)

    @property
    def is_land(self) -> bool:
        return bool(int(self.type_line.types) & _LAND)

    @property
    def is_legendary(self) -> bool:
        return bool(int(self.type_line.supertypes) & _LEGENDARY)

    @property
    def mana_value(self) -> int:
        """CR 202.3. Callers on the stack must add the chosen X themselves."""
        return self.mana_cost.mana_value

    # -- ability queries ----------------------------------------------------

    def has_keyword(self, keyword: str) -> bool:
        """Whether the object currently has a keyword ability.

        Case-insensitive because keyword names appear capitalised in some
        contexts and not others, and a mismatch here would silently drop
        flying.
        """
        needle = keyword.lower()
        return any(a.keyword.lower() == needle for a in self.abilities)

    def keyword_abilities(self, keyword: str) -> tuple[Ability, ...]:
        """Every ability for a keyword, so its parameter can be read.

        A creature can have protection from two different qualities, and Ward
        can appear more than once, so this returns all of them rather than the
        first.
        """
        needle = keyword.lower()
        return tuple(a for a in self.abilities if a.keyword.lower() == needle)

    @property
    def keywords(self) -> tuple[str, ...]:
        return tuple(a.keyword for a in self.abilities if a.keyword)

    def abilities_of(self, kind) -> tuple[Ability, ...]:
        return tuple(a for a in self.abilities if a.kind is kind)

    @property
    def alternative_costs(self) -> tuple:
        """Every alternative cost this object offers (CR 118.9)."""
        return tuple(
            a.alternative_cost for a in self.abilities if a.alternative_cost is not None
        )

    @property
    def additional_costs(self) -> tuple:
        """Every additional cost this object imposes (CR 118.8)."""
        return tuple(
            a.additional_cost for a in self.abilities if a.additional_cost is not None
        )

    @property
    def has_unparsed_abilities(self) -> bool:
        return any(a.unparsed for a in self.abilities)

    # -- derivation ---------------------------------------------------------

    def replace(self, **changes) -> Characteristics:
        """A modified copy. The layer system's only mutator."""
        return replace(self, **changes)

    def with_abilities(self, abilities: tuple[Ability, ...]) -> Characteristics:
        return replace(self, abilities=abilities)

    def adding_abilities(self, *abilities: Ability) -> Characteristics:
        return replace(self, abilities=self.abilities + abilities)

    def __str__(self) -> str:
        bits = [self.name or "(nameless)"]
        if self.has_mana_cost:
            bits.append(str(self.mana_cost))
        bits.append(str(self.type_line))
        if self.power is not None or self.toughness is not None:
            bits.append(f"{_show(self.power)}/{_show(self.toughness)}")
        if self.loyalty is not None:
            bits.append(f"[{self.loyalty}]")
        if self.defense is not None:
            bits.append(f"<{self.defense}>")
        return " ".join(bits)


def _show(value: int | None) -> str:
    return "*" if value is None else str(value)


def from_face(face, abilities: tuple[Ability, ...] = ()) -> Characteristics:
    """Build the printed characteristics of a card face (CR 613.2, the copiable base).

    ``power``/``toughness`` come back as None when the printed value is not a
    plain number (``*``, ``1+*``). That is a signal for a characteristic-defining
    ability in layer 7a (CR 613.4a), not a value of zero - treating it as zero
    is how a Tarmogoyf ends up a 0/1.

    Colors default to those implied by the mana cost (CR 202.2), overridden by a
    color indicator when one is printed (CR 202.2a).
    """
    colors = face.color_indicator if face.color_indicator is not None else face.colors
    return Characteristics(
        name=face.name,
        mana_cost=face.mana_cost,
        has_mana_cost=face.has_mana_cost,
        colors=colors,
        type_line=face.type_line,
        abilities=abilities,
        power=face.power_value,
        toughness=face.toughness_value,
        loyalty=face.loyalty_value,
        defense=face.defense_value,
        text=face.oracle_text,
    )
