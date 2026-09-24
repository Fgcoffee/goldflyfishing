"""The characteristics that belong to one card type only (CR 302.4, CR 306.5, CR 310.4).

Most characteristics belong to every object. Three do not: power and toughness
are a creature's (CR 302.4), loyalty is a planeswalker's (CR 306.5), defense is
a battle's (CR 310.4). A card can be *printed* with one of them without being
that type - CR 301.7 makes Vehicle an artifact subtype, and a Vehicle prints a
power and a toughness while staying a plain artifact until something crews it -
so "what is printed on the card" and "what the permanent has" come apart, and
the engine has to answer the second question rather than the first.

The two halves are shaped differently, on purpose.

**Power and toughness are computed.** The whole of CR 613 produces them, and
combat, the state-based actions and every filter that counts power read them.
So the rule that a noncreature permanent has neither is applied *once*, to the
characteristics the layer system has finished producing. Applying it any
earlier would break CR 208.3a - a "+1/+1 until end of turn" handed to a Vehicle
that is not yet a creature is still created, and still applies the moment it
becomes one - and it would make CR 301.7b, which gives a Vehicle its printed
power and toughness the instant it becomes a creature, need a second rule of
its own. Left to the end, both fall out: the printed values stay the layer-7
base throughout, and the answer is simply hidden while nothing is a creature.

**Loyalty and defense are not computed at all.** On the battlefield they *are*
the counters (CR 306.5c, 310.4c); nothing in the layer system produces them.
So they are read where they are used, which also keeps them clear of CR 306.5b
- the rule that puts the counters there in the first place has to see the
loyalty the permanent is entering with, at a moment when it has none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import CardType, Zone

if TYPE_CHECKING:
    from ..cr200_parts_of_a_card.characteristics import Characteristics
    from ..kernel.gameobject import GameObject


def settle_type_characteristics(obj: GameObject, chars: Characteristics) -> Characteristics:
    """Hide the characteristics this permanent's types do not give it.

    Called once per object with the finished output of the layer system, so
    that every effect that modified power or toughness has already applied.

    CR 208.3: a noncreature permanent has no power and no toughness, whatever
    is printed on the card. CR 301.7a says the same thing of a Vehicle from the
    other side - the printed numbers are the Vehicle's only while it is also a
    creature - and CR 301.7b hands them back the instant it becomes one.

    Only the battlefield is touched. Off it, CR 208.3 keeps the printed values
    for any object that has them, which is why a Vehicle in a graveyard is
    still a 3/3 card to anything that asks.
    """
    if obj.zone is not Zone.BATTLEFIELD:
        return chars
    if chars.is_creature:
        return chars
    if chars.power is None and chars.toughness is None:
        return chars
    return chars.replace(power=None, toughness=None)


def loyalty(obj: GameObject, chars: Characteristics) -> int | None:
    """This object's loyalty (CR 306.5).

    CR 306.5c: on the battlefield it is the number of loyalty counters, not the
    number printed - which is what makes "target planeswalker with loyalty 3 or
    less" a question about the board rather than about the card. CR 306.5a
    leaves a planeswalker card anywhere else with its printed loyalty.

    CR 306.5 makes loyalty a planeswalker's alone, so a permanent that is not
    one has none even if it was printed with a loyalty and has since lost the
    type.
    """
    if obj.zone is not Zone.BATTLEFIELD:
        return chars.loyalty
    if not chars.has_type(CardType.PLANESWALKER):
        return None
    return obj.counter_count("loyalty")


def defense(obj: GameObject, chars: Characteristics) -> int | None:
    """This object's defense (CR 310.4).

    CR 310.4c on the battlefield, CR 310.4a everywhere else - the same shape as
    loyalty, and for the same reason: the counters are the number, and the
    printed value only says how many the battle arrived with (CR 310.4b).

    Unlike CR 306.5, CR 310.4 does not say defense belongs to battles alone, so
    a permanent that is not a battle is left with whatever it has rather than
    being stripped.
    """
    if obj.zone is not Zone.BATTLEFIELD or not chars.has_type(CardType.BATTLE):
        return chars.defense
    return obj.counter_count("defense")


def entering_counters(chars: Characteristics) -> tuple[tuple[str, int], ...]:
    """The counters a permanent of this type enters the battlefield with.

    CR 310.4b spells it out as an intrinsic ability - a battle enters with
    defense counters equal to its printed defense - and CR 306.5b says the same
    of a planeswalker and its loyalty. Intrinsic means it applies however the
    permanent arrives, so a reanimated planeswalker gets its loyalty exactly as
    a cast one does; it is not a property of resolving as a spell.

    Asked of the characteristics the permanent is entering *with*, before it is
    on the battlefield - CR 306.5c would otherwise answer with the counters it
    does not have yet.
    """
    out: list[tuple[str, int]] = []
    if chars.has_type(CardType.PLANESWALKER) and chars.loyalty is not None:
        out.append(("loyalty", chars.loyalty))
    if chars.has_type(CardType.BATTLE) and chars.defense is not None:
        out.append(("defense", chars.defense))
    return tuple(out)


__all__ = ["defense", "entering_counters", "loyalty", "settle_type_characteristics"]
