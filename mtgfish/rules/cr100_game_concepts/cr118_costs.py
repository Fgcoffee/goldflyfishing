"""Costs and the CR 601.2f total-cost calculation.

A cost is a list of components. Mana is only one of them: tapping, sacrificing,
discarding, paying life, exiling from a graveyard, and removing counters are
all costs, and all of them have to be payable *before* anything is paid, since
a cost that cannot be paid in full cannot be paid at all (CR 601.2h).

The total-cost arithmetic is order-sensitive and gets misremembered constantly.
CR 601.2f: start from the mana cost or alternative cost, add every additional
cost and every cost increase, and only then apply cost reductions. Doing
reductions first would let a Trinisphere-style tax be dodged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ..kernel.enums import Zone
from ..kernel.query import ZERO, ObjectFilter, Value
from .cr106_mana import ZERO_COST, ManaCost
from .cr107_numbers import effect_result, is_undeterminable


class CostKind(IntEnum):
    MANA = 0
    #: {T} and {Q} in an activation cost (CR 107.5, 107.6).
    TAP_SELF = 1
    UNTAP_SELF = 2
    TAP_OTHER = 3
    UNTAP_OTHER = 4

    SACRIFICE = 10
    DISCARD = 11
    EXILE_FROM_GRAVEYARD = 12
    EXILE_FROM_HAND = 13
    EXILE_FROM_LIBRARY = 14
    RETURN_TO_HAND = 15
    PUT_ON_LIBRARY = 16
    MILL = 17
    REVEAL = 18
    #: "Exile this artifact", "Exile a creature you control": the cost is a
    #: permanent. Not a graveyard card, which is what these were charged as.
    EXILE_FROM_BATTLEFIELD = 19

    PAY_LIFE = 30
    #: CR 107.14: {E} is one energy counter, paid by removing it from the
    #: player. Energy is not mana and never enters a mana pool.
    PAY_ENERGY = 31
    REMOVE_COUNTERS = 32
    PUT_COUNTERS = 33
    #: CR 107.7: the loyalty symbol in a planeswalker's activation cost. A
    #: positive symbol puts that many loyalty counters on, a negative one
    #: removes them, so this is the one cost amount that is signed.
    #: CR 606 is the ability; this is the symbol.
    LOYALTY = 34

    UNATTACH = 40
    #: Costs the engine understands the shape of but that need a player choice
    #: with no mechanical effect (e.g. "choose a color").
    CHOOSE = 41

    #: The parser could not read this cost component. An ability carrying one
    #: is never activatable, which is the safe failure mode.
    UNPARSED = 99


@dataclass(frozen=True, slots=True)
class CostComponent:
    kind: CostKind
    mana: ManaCost = ZERO_COST
    amount: Value = ZERO
    #: Multiplies the component: "{2} for each creature they control that's
    #: attacking you". Without it an attack tax is charged once no matter how
    #: many creatures attack, which is a completely different card.
    scale: Value | None = None
    filter: ObjectFilter | None = None
    counter_type: str = ""
    text: str = ""

    @property
    def is_mana(self) -> bool:
        return self.kind is CostKind.MANA

    def __str__(self) -> str:
        if self.kind is CostKind.MANA:
            return str(self.mana)
        if self.kind is CostKind.TAP_SELF:
            return "{T}"
        if self.kind is CostKind.UNTAP_SELF:
            return "{Q}"
        if self.text:
            return self.text
        base = self.kind.name.lower().replace("_", " ")
        return f"{base} {self.amount}" if not self.amount.is_constant or self.amount.constant else base


#: Costs paid by consuming something - a permanent, a card, a counter. An
#: amount of zero is never what one of these means: a card that says "sacrifice
#: a creature" always sacrifices one. Zero here has only ever meant a clause
#: that forgot to set the amount, and the consequence is severe out of all
#: proportion to the typo - the cost is payable, nothing is consumed, so the
#: ability is free and can be activated for ever. That is what turned a
#: 1,000-game run into forty games overnight.
CONSUMING_COSTS = frozenset(
    {
        CostKind.SACRIFICE,
        CostKind.DISCARD,
        CostKind.EXILE_FROM_BATTLEFIELD,
        CostKind.EXILE_FROM_GRAVEYARD,
        CostKind.EXILE_FROM_HAND,
        CostKind.EXILE_FROM_LIBRARY,
        CostKind.RETURN_TO_HAND,
        CostKind.PUT_ON_LIBRARY,
        CostKind.MILL,
        CostKind.REMOVE_COUNTERS,
        CostKind.TAP_OTHER,
        CostKind.UNTAP_OTHER,
    }
)


#: The zone each exile cost takes its card from. The kind *is* the zone: an
#: exile cost paid from anywhere else is a different card, and "Exile this
#: artifact" paid with a graveyard card kept the artifact and its effect both.
EXILE_ZONES = {
    CostKind.EXILE_FROM_BATTLEFIELD: Zone.BATTLEFIELD,
    CostKind.EXILE_FROM_HAND: Zone.HAND,
    CostKind.EXILE_FROM_GRAVEYARD: Zone.GRAVEYARD,
    CostKind.EXILE_FROM_LIBRARY: Zone.LIBRARY,
}


def required_amount(component: CostComponent, evaluated: int) -> int:
    """What a cost component actually charges, given its evaluated amount.

    Every non-mana cost passes through here, on the dry run and on the
    payment alike, which makes it the one place the number rules can be
    applied to a cost.

    Floors a consuming cost at one. See ``CONSUMING_COSTS`` for why: the
    alternative is a free ability, and a free ability is an infinite loop
    rather than a slightly wrong card.

    Everything else is floored at zero, because a cost is the result of a
    calculation and CR 107.1b does not let one go negative. Paying a negative
    amount is not a discount, it is the reverse of the cost: an energy cost
    that evaluated to -3 was charged as ``player.energy -= -3`` and handed the
    player three energy. A loyalty cost is exempt and stays signed - CR 107.7
    makes a negative loyalty symbol mean removing counters, which is what a
    planeswalker's minus ability is.

    Raises rather than returning for an undeterminable amount: CR 903.4f makes
    such a cost unpayable, and CR 118.6 makes attempting to pay an unpayable
    cost an illegal action. Returning the number would make the cost free,
    which is the opposite of what the rule says.
    """
    if is_undeterminable(evaluated):
        # Deliberately the same error the rest of cost payment raises, so the
        # cast or activation is abandoned and rewound (CR 601.2h) and the
        # legality check that dry-runs the cost simply stops offering it.
        from ..cr600_spells_and_abilities.cr601_casting import CastError

        reason = getattr(evaluated, "reason", "a number that cannot be determined")
        raise CastError(f"unpayable cost: it refers to {reason}")
    if component.kind in CONSUMING_COSTS:
        return max(1, evaluated)
    if component.kind is CostKind.LOYALTY:
        return evaluated
    return effect_result(evaluated)


@dataclass(frozen=True, slots=True)
class Cost:
    """A complete cost: everything that must be paid, mana and otherwise."""

    components: tuple[CostComponent, ...] = ()
    #: "Discard a card or pay 3 life" - a cost written as a choice between
    #: whole costs. Not a list of components, which would mean paying both;
    #: exactly one of these is paid. Empty for the ordinary case.
    choices: tuple = ()

    @classmethod
    def mana(cls, cost: ManaCost | str) -> Cost:
        parsed = ManaCost.parse(cost) if isinstance(cost, str) else cost
        return cls((CostComponent(CostKind.MANA, mana=parsed),))

    @classmethod
    def tap(cls) -> Cost:
        return cls((CostComponent(CostKind.TAP_SELF),))

    @property
    def mana_component(self) -> ManaCost:
        """All mana components combined into one cost."""
        total = ZERO_COST
        for component in self.components:
            if component.is_mana:
                total = total.plus(component.mana)
        return total

    @property
    def non_mana_components(self) -> tuple[CostComponent, ...]:
        return tuple(c for c in self.components if not c.is_mana)

    @property
    def requires_tapping(self) -> bool:
        return any(c.kind is CostKind.TAP_SELF for c in self.components)

    @property
    def is_unparsed(self) -> bool:
        return any(c.kind is CostKind.UNPARSED for c in self.components)

    @property
    def is_free(self) -> bool:
        return not self.components

    def plus(self, other: Cost) -> Cost:
        return Cost(self.components + other.components)

    def with_component(self, component: CostComponent) -> Cost:
        return Cost(self.components + (component,))

    def __bool__(self) -> bool:
        return bool(self.components)

    def __str__(self) -> str:
        return ", ".join(str(c) for c in self.components) if self.components else "{0}"


FREE = Cost(())
TAP_COST = Cost.tap()


# ---------------------------------------------------------------------------
# Alternative and additional costs (CR 118.8, 118.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlternativeCost:
    """A cost paid *instead of* the mana cost (CR 118.9).

    Flashback, Evoke, Overload, "cast without paying its mana cost". Two rules
    make these more than a discount:

    CR 118.9a - only **one** alternative cost may be applied to a spell.
    CR 118.9c - it does not change the spell's mana cost, only what has to be
    paid. Anything that later asks for the spell's mana cost still sees the
    printed value, which is why a Flashbacked spell still has its original
    mana value.
    """

    cost: Cost
    #: Where the spell must be cast from for this to be available, e.g.
    #: Flashback from a graveyard. ``None`` means anywhere.
    from_zone: object | None = None
    #: CR 601.2b: when this option may be chosen at all ("during your declare
    #: blockers step"). ``None`` means whenever the spell could be cast.
    condition: object | None = None
    #: CR 702.190a: some alternative costs let the spell be cast any time its
    #: controller could cast an instant, whatever its own timing.
    instant_speed: bool = False
    #: CR 702.190b: a permanent spell cast this way enters tapped and
    #: attacking whatever the creature returned to pay for it was attacking.
    enters_tapped_and_attacking: bool = False
    #: The keyword this came from, for the log and the coverage report.
    keyword: str = ""
    text: str = ""

    def __str__(self) -> str:
        return self.text or f"{self.keyword or 'alternative cost'} {self.cost}"


@dataclass(frozen=True, slots=True)
class AdditionalCost:
    """A cost paid *alongside* the mana cost (CR 118.8).

    Kicker, casualty, "as an additional cost, sacrifice a creature". Unlike
    alternative costs, any number may apply (CR 118.8a), and CR 118.8d says
    they do not change the spell's mana cost either.
    """

    cost: Cost
    #: CR 118.8b: some are optional, and the controller decides at CR 601.2b.
    optional: bool = False
    keyword: str = ""
    text: str = ""

    def __str__(self) -> str:
        return self.text or f"{self.keyword or 'additional cost'} {self.cost}"


# ---------------------------------------------------------------------------
# Total cost (CR 601.2f)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TotalCost:
    """The cost actually being paid, built up in CR 601.2f order.

    Kept mutable and staged rather than collapsed into one number, because the
    intermediate values are visible to the game: "spells cost {1} more" applies
    to the increased total, while cost reduction is floored so it can never eat
    a colored requirement.
    """

    #: The mana cost, or the alternative cost being paid instead (CR 601.2f).
    base: ManaCost = ZERO_COST
    #: Non-mana costs from the card itself plus any additional costs.
    additional: tuple[CostComponent, ...] = ()
    #: Generic mana added by cost-increase effects.
    increase: int = 0
    #: Generic mana removed by cost-reduction effects.
    reduction: int = 0
    #: The value chosen for X (CR 601.2b), substituted before reductions apply.
    x_value: int = 0
    #: True when an alternative cost replaced the mana cost entirely.
    is_alternative: bool = False
    #: Reductions expressed as specific mana rather than an amount of generic
    #: (CR 118.7b-g), which behave differently and are applied separately.
    mana_reduction: ManaCost = ZERO_COST
    #: CR 118.6: an object with no mana cost has an *unpayable* cost, which is
    #: not the same as a cost of zero. Only an alternative cost makes it
    #: castable (CR 118.6a).
    #:
    #: This is not the only way a cost is unpayable. A cost whose amount
    #: cannot be determined - CR 903.4f's commander colour identity with no
    #: commander - is unpayable too, and is refused by ``required_amount``
    #: when the amount is evaluated rather than flagged here, because nothing
    #: at this level has a game to evaluate it against.
    unpayable: bool = False

    def add_additional(self, component: CostComponent) -> None:
        self.additional = self.additional + (component,)

    def add_mana(self, cost: ManaCost) -> None:
        """An additional cost that happens to be mana, e.g. kicker."""
        self.base = self.base.plus(cost)

    @property
    def is_payable(self) -> bool:
        """CR 118.6: unpayable unless an alternative cost replaced it."""
        return not self.unpayable or self.is_alternative

    def final_mana(self) -> ManaCost:
        """The mana that must actually be paid.

        CR 601.2f order: X is substituted first (it becomes generic mana), then
        additional costs and increases, and only then reductions. CR 118.9d
        applies all of that to the alternative cost when one is being paid, so
        an alternative cost is not a way to dodge a tax.
        """
        cost = self.base.substitute_x(self.x_value)
        cost = cost.increased_by(self.increase)
        if self.mana_reduction:
            cost = cost.reduced_by_mana(self.mana_reduction)
        return cost.reduced_by(self.reduction)

    def as_cost(self) -> Cost:
        """The whole thing as a single Cost, for payment."""
        components = (CostComponent(CostKind.MANA, mana=self.final_mana()),)
        return Cost(components + self.additional)

    def __str__(self) -> str:
        return str(self.as_cost())
