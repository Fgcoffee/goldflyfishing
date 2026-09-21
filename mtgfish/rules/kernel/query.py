"""Describing sets of objects, sets of players, and numbers that vary.

Almost every line of Magic text names a set of objects ("each creature you
control with power 3 or greater"), a set of players ("each opponent"), or a
number that depends on the board ("equal to the number of Swamps you control").
Those three shapes recur so relentlessly that getting them right here is worth
more than any individual card.

These are pure data - no closures - so the parser can build them, tests can
compare them, and the whole structure serialises for the native port.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from .enums import CardType, Color, Supertype, Zone
from .ids import NO_OBJECT, ObjectId, PlayerId


class Comparison(IntEnum):
    EQ = 0
    NE = 1
    LT = 2
    LE = 3
    GT = 4
    GE = 5

    def holds(self, left: int, right: int) -> bool:
        if self is Comparison.EQ:
            return left == right
        if self is Comparison.NE:
            return left != right
        if self is Comparison.LT:
            return left < right
        if self is Comparison.LE:
            return left <= right
        if self is Comparison.GT:
            return left > right
        return left >= right

    def __str__(self) -> str:
        return {
            Comparison.EQ: "exactly",
            Comparison.NE: "other than",
            Comparison.LT: "less than",
            Comparison.LE: "or less",
            Comparison.GT: "greater than",
            Comparison.GE: "or greater",
        }[self]


# ---------------------------------------------------------------------------
# Dynamic values
# ---------------------------------------------------------------------------


class ValueKind(IntEnum):
    """How a number is arrived at."""

    CONSTANT = 0
    #: The X chosen when the spell was cast (CR 601.2b).
    X = 1
    #: Count of objects matching a filter.
    COUNT = 2
    #: A characteristic of a specific object.
    POWER = 3
    TOUGHNESS = 4
    MANA_VALUE = 5
    #: Player-scoped quantities.
    LIFE_TOTAL = 6
    CARDS_IN_HAND = 7
    POISON_COUNTERS = 8
    ENERGY = 9
    #: Counters of a named kind on an object.
    COUNTERS = 10
    #: Arithmetic over other values, so "twice the number of Elves plus one"
    #: composes rather than needing its own kind.
    SUM = 11
    PRODUCT = 12
    DIFFERENCE = 13
    HALF_ROUNDED_UP = 14
    HALF_ROUNDED_DOWN = 15
    MAXIMUM = 16
    MINIMUM = 17
    #: A characteristic taken across every object matching a filter: "the
    #: greatest mana value among permanents you control". Distinct from
    #: MAXIMUM, which compares a fixed list of operands - here the *number* of
    #: things being compared is a game-state question.
    #:
    #: ``filter`` says which objects; ``operands[0]`` says which characteristic
    #: to read, evaluated once per object. Reusing a Value for the second half
    #: means POWER, TOUGHNESS and MANA_VALUE need no special casing.
    GREATEST_AMONG = 18
    LEAST_AMONG = 19
    #: "Leave this characteristic alone." Needed because a CDA can define one
    #: half of a creature's P/T and not the other - "Mendicant Core's power is
    #: equal to the number of artifacts you control" leaves the printed
    #: toughness standing. Without a sentinel the missing half reads as zero
    #: and every such creature becomes an X/0 that dies immediately.
    UNCHANGED = 20
    #: CR 700.5: mana symbols of a colour among the mana costs of permanents
    #: a player controls. Not a count of permanents - one Gray Merchant is
    #: worth two, and a hybrid symbol counts for both of its colours.
    DEVOTION = 22
    #: How many distinct colours appear among a set of objects. Not a count of
    #: the objects: two green permanents are one colour.
    COLOURS_AMONG = 23
    #: CR 601.2g: mana actually spent casting a spell, which differs from its
    #: mana value whenever X or a cost modification was involved.
    MANA_SPENT = 24
    #: The total power of whatever was consumed to pay this ability's cost.
    #: Station is the reason: "put charge counters equal to that creature's
    #: power" asks about the creature the cost tapped, which nothing else in
    #: the resolution has any way to name.
    COST_PAID_POWER = 21
    #: How big the event that triggered this ability was. "Whenever this deals
    #: combat damage to a player, create that many Treasure tokens" has no
    #: other way to ask - the number is a fact about the event, not about any
    #: object still on the battlefield, and by resolution the damage is over.
    EVENT_AMOUNT = 25
    #: How many times something happened this turn: "for each spell you've
    #: cast this turn", "for each time it has attacked this turn". The game
    #: keeps the tally; there was a Condition that could ask whether it
    #: happened and no Value that could ask how often.
    EVENT_COUNT_THIS_TURN = 26
    #: A characteristic summed across every object matching a filter: "the
    #: total power of creatures you control". The third way to ask about a
    #: set, alongside GREATEST_AMONG and LEAST_AMONG, and read the same way -
    #: ``filter`` says which objects, ``operands[0]`` which characteristic.
    TOTAL_AMONG = 27
    #: CR 903.4: how many colours are in the commander's colour identity.
    #: A deck-level fact rather than a board one - it counts mana symbols in
    #: rules text as well as the card's own colours, so it is not the same
    #: question as COLOURS_AMONG and cannot be answered by counting
    #: permanents.
    COMMANDER_COLOUR_IDENTITY = 28


@dataclass(frozen=True, slots=True)
class Value:
    """A number that may depend on the game state.

    A plain integer is ``Value(ValueKind.CONSTANT, constant=3)``; use
    ``Value.of(3)``. Evaluation lives in the engine, not here, because it needs
    the game state.
    """

    kind: ValueKind = ValueKind.CONSTANT
    constant: int = 0
    #: For COUNT and characteristic lookups.
    filter: ObjectFilter | None = None
    players: PlayerFilter | None = None
    counter_type: str = ""
    #: Which colour a DEVOTION value counts. Several bits means devotion to a
    #: combination, which counts a symbol once if it matches any of them
    #: (CR 700.5).
    colors: Color = Color.NONE
    #: Operands for the arithmetic kinds.
    operands: tuple[Value, ...] = ()
    #: Whether a characteristic lookup refers to the object the effect is
    #: applying to rather than the ability's source. Opalescence's "has base
    #: power and toughness each equal to its mana value" means the enchantment
    #: being animated, not Opalescence.
    of_affected: bool = False
    #: For EVENT_COUNT_THIS_TURN: which events to total, as plain ints so
    #: this module need not import the event enum.
    event_kinds: tuple[int, ...] = ()

    @classmethod
    def of(cls, amount: int) -> Value:
        return cls(ValueKind.CONSTANT, constant=amount)

    @property
    def is_constant(self) -> bool:
        return self.kind is ValueKind.CONSTANT

    def __str__(self) -> str:
        if self.kind is ValueKind.CONSTANT:
            return str(self.constant)
        if self.kind is ValueKind.UNCHANGED:
            return "unchanged"
        if self.kind is ValueKind.COST_PAID_POWER:
            return "the power of the creature tapped to pay for this"
        if self.kind is ValueKind.X:
            return "X"
        if self.kind is ValueKind.COUNT:
            return f"the number of {self.filter}"
        if self.kind is ValueKind.MANA_SPENT:
            return "the mana spent to cast it"
        if self.kind is ValueKind.COLOURS_AMONG:
            return f"the number of colors among {self.filter}"
        if self.kind is ValueKind.DEVOTION:
            names = " and ".join(c.name.lower() for c in self.colors) or "nothing"
            return f"your devotion to {names}"
        if self.kind in (ValueKind.GREATEST_AMONG, ValueKind.LEAST_AMONG):
            word = (
                "greatest" if self.kind is ValueKind.GREATEST_AMONG else "least"
            )
            what = str(self.operands[0]) if self.operands else "value"
            what = what.replace("this permanent's ", "").replace("its ", "")
            return f"the {word} {what} among {self.filter}"
        if self.kind is ValueKind.COUNTERS:
            whose = "its" if self.of_affected else "this permanent's"
            return f"the number of {self.counter_type or '+1/+1'} counters on {whose}"

        whose = "its" if self.of_affected else "this permanent's"
        characteristic = {
            ValueKind.POWER: f"{whose} power",
            ValueKind.TOUGHNESS: f"{whose} toughness",
            ValueKind.MANA_VALUE: f"{whose} mana value",
        }.get(self.kind)
        if characteristic is not None:
            return characteristic

        player = str(self.players) if self.players is not None else "your"
        player_value = {
            ValueKind.LIFE_TOTAL: f"{player} life total",
            ValueKind.CARDS_IN_HAND: f"the cards in {player} hand",
            ValueKind.POISON_COUNTERS: f"{player} poison counters",
            ValueKind.ENERGY: f"{player} energy",
        }.get(self.kind)
        if player_value is not None:
            return player_value

        joiner = {
            ValueKind.SUM: " plus ",
            ValueKind.PRODUCT: " times ",
            ValueKind.DIFFERENCE: " minus ",
            ValueKind.MAXIMUM: " or ",
            ValueKind.MINIMUM: " or ",
        }.get(self.kind)
        operands = [str(o) for o in self.operands]
        if joiner is not None:
            # ``constant`` is part of the arithmetic for these kinds - a
            # multiplier for PRODUCT and an addend for SUM - and leaving it out
            # made Affinity's "-1 times the number of artifacts you control"
            # print as "the number of artifacts you control", so a correct cost
            # *reduction* read as an increase. A round-trip that accuses a
            # working card is as expensive as one that clears a broken one.
            if self.kind is ValueKind.PRODUCT and self.constant not in (0, 1):
                operands = [str(self.constant), *operands]
            elif self.kind is ValueKind.SUM and self.constant:
                operands = [*operands, str(self.constant)]
            return joiner.join(operands)
        if self.kind is ValueKind.HALF_ROUNDED_UP:
            return f"half of {operands[0] if operands else '?'}, rounded up"
        if self.kind is ValueKind.HALF_ROUNDED_DOWN:
            return f"half of {operands[0] if operands else '?'}, rounded down"
        return f"{self.kind.name.lower()}({', '.join(operands)})"


ZERO = Value.of(0)
ONE = Value.of(1)


@dataclass(frozen=True, slots=True)
class NumericConstraint:
    """A comparison against a possibly-dynamic value, e.g. "power 3 or greater"."""

    comparison: Comparison
    value: Value

    @classmethod
    def at_least(cls, amount: int) -> NumericConstraint:
        return cls(Comparison.GE, Value.of(amount))

    @classmethod
    def at_most(cls, amount: int) -> NumericConstraint:
        return cls(Comparison.LE, Value.of(amount))

    @classmethod
    def exactly(cls, amount: int) -> NumericConstraint:
        return cls(Comparison.EQ, Value.of(amount))

    def __str__(self) -> str:
        # "3 or greater" and "greater than 3" put the operator on opposite
        # sides, and oracle text uses both.
        if self.comparison in (Comparison.GE, Comparison.LE):
            return f"{self.value} {self.comparison}"
        return f"{self.comparison} {self.value}"


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


class PlayerScope(IntEnum):
    """Who a spell or ability is talking about.

    Resolved relative to the ability's controller unless stated otherwise
    (CR 109.5: "you" is the controller of the spell or ability).
    """

    YOU = 0
    OPPONENT = 1
    EACH_PLAYER = 2
    EACH_OPPONENT = 3
    TARGET_PLAYER = 4
    TARGET_OPPONENT = 5
    ACTIVE_PLAYER = 6
    CONTROLLER_OF = 7  # of some referenced object
    OWNER_OF = 8
    SPECIFIC = 9  # a resolved PlayerId, used once choices are made
    MONARCH = 10
    DEFENDING_PLAYER = 11


@dataclass(frozen=True, slots=True)
class PlayerFilter:
    scope: PlayerScope = PlayerScope.YOU
    #: Populated once a choice has been made or a reference resolved.
    specific: PlayerId | None = None
    reference: ObjectId = NO_OBJECT
    #: Further narrowing, e.g. "each opponent who controls a creature".
    controls: ObjectFilter | None = None
    life_constraint: NumericConstraint | None = None

    @property
    def is_single(self) -> bool:
        return self.scope not in (
            PlayerScope.EACH_PLAYER,
            PlayerScope.EACH_OPPONENT,
        )

    def __str__(self) -> str:
        names = {
            PlayerScope.YOU: "you",
            PlayerScope.OPPONENT: "an opponent",
            PlayerScope.EACH_PLAYER: "each player",
            PlayerScope.EACH_OPPONENT: "each opponent",
            PlayerScope.TARGET_PLAYER: "target player",
            PlayerScope.TARGET_OPPONENT: "target opponent",
            PlayerScope.ACTIVE_PLAYER: "the active player",
            PlayerScope.CONTROLLER_OF: "its controller",
            PlayerScope.OWNER_OF: "its owner",
            PlayerScope.MONARCH: "the monarch",
            PlayerScope.DEFENDING_PLAYER: "the defending player",
        }
        base = names.get(self.scope, self.scope.name.lower().replace("_", " "))
        if self.controls is not None:
            base += f" who controls {self.controls.describe()}"
        if self.life_constraint is not None:
            base += f" with life {self.life_constraint}"
        return base


YOU = PlayerFilter(PlayerScope.YOU)
EACH_OPPONENT = PlayerFilter(PlayerScope.EACH_OPPONENT)
EACH_PLAYER = PlayerFilter(PlayerScope.EACH_PLAYER)


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


class ControllerRelation(IntEnum):
    ANY = 0
    YOU = 1
    OPPONENT = 2
    #: Same or different controller as the ability's source.
    SAME_AS_SOURCE = 3
    #: Controlled by a specific player, once resolved.
    SPECIFIC = 4
    DEFENDING_PLAYER = 5


@dataclass(frozen=True, slots=True)
class ObjectFilter:
    """A description of which objects something applies to.

    Type constraints come in three flavours because English does:

    ``types_any``   "creature or enchantment" - at least one bit must match
    ``types_all``   "artifact creature" - every bit must match
    ``types_none``  "nonland permanent" - no bit may match

    The same three-way split applies to subtypes and colors. Getting this wrong
    is how a simulator ends up letting a Wrath of God kill a land.
    """

    # -- types --------------------------------------------------------------
    types_any: CardType = CardType.NONE
    types_all: CardType = CardType.NONE
    types_none: CardType = CardType.NONE
    supertypes_all: Supertype = Supertype.NONE
    supertypes_none: Supertype = Supertype.NONE
    subtypes_any: tuple[str, ...] = ()
    subtypes_all: tuple[str, ...] = ()
    subtypes_none: tuple[str, ...] = ()

    # -- colors (CR 105) ----------------------------------------------------
    colors_any: Color = Color.NONE
    colors_all: Color = Color.NONE
    colors_none: Color = Color.NONE
    #: "colorless" is a real constraint, distinct from "no color constraint".
    must_be_colorless: bool = False
    #: "permanents that are one or more colors" - the opposite question, and
    #: not the same as "not colourless" being unset, which asks nothing.
    must_be_coloured: bool = False
    must_be_multicolored: bool = False
    must_be_monocolored: bool = False

    # -- identity -----------------------------------------------------------
    named: tuple[str, ...] = ()
    not_named: tuple[str, ...] = ()
    #: "another" - excludes the source of the ability (CR 109.2).
    other_than_source: bool = False
    #: "this creature" and friends - only the source itself.
    source_only: bool = False
    #: CR 115.4: "any target" - this filter's objects, *or* a player. Kept as
    #: a flag on the object filter rather than a separate field because every
    #: place that asks "what may this target?" already has the filter to hand.
    includes_players: bool = False
    #: "it", "them", "that creature" - whatever the resolution last acted on.
    #: Distinct from ``source_only`` because they are usually different
    #: objects: "Destroy target creature. Its controller loses 2 life" means
    #: the *target's* controller, not this card's.
    remembered: bool = False
    specific: tuple[ObjectId, ...] = ()

    # -- zone and control ---------------------------------------------------
    zones: frozenset[Zone] = frozenset({Zone.BATTLEFIELD})
    #: "the top card of your library", "the top three cards of their library".
    #: Zero means no position constraint. A library is ordered (CR 401.2), so
    #: this is a real narrowing and not a shorthand for "a card in a library".
    from_top: int = 0
    controller: ControllerRelation = ControllerRelation.ANY
    controller_specific: PlayerId | None = None
    owner: ControllerRelation = ControllerRelation.ANY

    # -- battlefield state --------------------------------------------------
    tapped: bool | None = None
    attacking: bool | None = None
    blocking: bool | None = None
    blocked: bool | None = None
    face_down: bool | None = None
    is_token: bool | None = None
    #: "creatures of the chosen type", "permanents of the chosen color" -
    #: matched against the choice recorded on the ability's source, so the
    #: filter cannot be resolved until there is a game to ask.
    of_chosen_type: bool = False
    of_chosen_color: bool = False
    #: "creatures that aren't of the chosen type". Its own flag rather than
    #: ``of_chosen_type = False``, which is indistinguishable from not asking
    #: at all - and a filter that does not ask matches everything.
    not_of_chosen_type: bool = False
    #: An ability on the stack (CR 113.7) rather than a card or permanent.
    #: Not expressible any other way: an ability has no card type, so every
    #: type-based constraint is silent about it.
    is_ability: bool | None = None
    is_commander: bool | None = None
    phased_out: bool | None = None
    #: "a creature that entered the battlefield this turn", etc.
    entered_this_turn: bool | None = None
    summoning_sick: bool | None = None

    # -- numeric ------------------------------------------------------------
    power: NumericConstraint | None = None
    toughness: NumericConstraint | None = None
    mana_value: NumericConstraint | None = None
    loyalty: NumericConstraint | None = None

    # -- counters and attachments -------------------------------------------
    has_counter: str = ""
    counter_constraint: NumericConstraint | None = None
    attached_to: ObjectFilter | None = None
    has_attached: ObjectFilter | None = None

    # -- abilities ----------------------------------------------------------
    has_keyword: tuple[str, ...] = ()
    lacks_keyword: tuple[str, ...] = ()

    # -- targeting (CR 115) -------------------------------------------------
    #: How many of these to pick. ``None`` means "all matching", which is what
    #: mass effects like Wrath of God want.
    count: Value | None = None
    up_to: bool = False

    def describe(self, *, quantified: bool = True) -> str:
        """Say every constraint this filter carries, in English.

        Completeness matters more than elegance here. This is what the sandbox
        shows when asking "did the parser read this card correctly?", and a
        constraint that is held but not printed renders identically to one the
        parser dropped - which would make the whole review worthless. So every
        field that narrows the match appears, even the awkward ones.
        """
        parts: list[str] = []

        quantity = self._quantity() if quantified else []
        parts.extend(quantity)
        if self.other_than_source:
            parts.append("another")
        parts.extend(self._colors())
        parts.extend(self._types())
        if parts == quantity:
            parts.append("object")

        parts.extend(self._identity())
        parts.extend(self._control())
        parts.extend(self._numeric())
        parts.extend(self._state())
        parts.extend(self._attachments())
        parts.extend(self._zone_phrase())
        return " ".join(parts)

    # -- describe() in pieces, so no constraint has to fight for room --------

    def _quantity(self) -> list[str]:
        if self.count is None:
            return ["all"] if not self.source_only and not self.remembered else []
        return [f"up to {self.count}"] if self.up_to else [str(self.count)]

    def _colors(self) -> list[str]:
        parts: list[str] = []
        if self.colors_any:
            parts.append(" or ".join(c.name.lower() for c in self.colors_any))
        if self.colors_all:
            parts.extend(c.name.lower() for c in self.colors_all)
        if self.colors_none:
            parts.extend(f"non{c.name.lower()}" for c in self.colors_none)
        if self.must_be_colorless:
            parts.append("colorless")
        if self.must_be_multicolored:
            parts.append("multicolored")
        if self.must_be_monocolored:
            parts.append("monocolored")
        return parts

    def _types(self) -> list[str]:
        parts: list[str] = []
        if self.supertypes_all:
            parts.extend(t.name.lower() for t in self.supertypes_all)
        if self.supertypes_none:
            parts.extend(f"non{t.name.lower()}" for t in self.supertypes_none)
        if self.subtypes_any:
            parts.append(" or ".join(self.subtypes_any))
        if self.subtypes_all:
            parts.extend(self.subtypes_all)
        if self.subtypes_none:
            parts.extend(f"non-{t}" for t in self.subtypes_none)
        if self.types_all:
            parts.extend(t.name.lower() for t in self.types_all)
        if self.types_any:
            parts.append(" or ".join(t.name.lower() for t in self.types_any))
        if self.types_none:
            parts.extend(f"non{t.name.lower()}" for t in self.types_none)
        return parts

    def _identity(self) -> list[str]:
        parts: list[str] = []
        if self.source_only:
            parts.append("(this permanent itself)")
        if self.remembered:
            parts.append("(whatever was just referred to)")
        if self.named:
            parts.append("named " + " or ".join(self.named))
        if self.not_named:
            parts.append("not named " + " or ".join(self.not_named))
        if self.includes_players:
            parts.append("or player")
        return parts

    def _control(self) -> list[str]:
        parts: list[str] = []
        if self.controller is ControllerRelation.YOU:
            parts.append("you control")
        elif self.controller is ControllerRelation.OPPONENT:
            parts.append("an opponent controls")
        elif self.controller is ControllerRelation.SPECIFIC:
            parts.append("that player controls")
        if self.owner is ControllerRelation.YOU:
            parts.append("you own")
        elif self.owner is ControllerRelation.OPPONENT:
            parts.append("an opponent owns")
        return parts

    def _numeric(self) -> list[str]:
        parts: list[str] = []
        for label, constraint in (
            ("power", self.power),
            ("toughness", self.toughness),
            ("mana value", self.mana_value),
            ("loyalty", self.loyalty),
        ):
            if constraint is not None:
                parts.append(f"with {label} {constraint}")
        if self.of_chosen_type:
            parts.append("of the chosen type")
        if self.not_of_chosen_type:
            parts.append("that aren't of the chosen type")
        if self.of_chosen_color:
            parts.append("of the chosen colour")
        if self.has_counter:
            if self.counter_constraint is not None:
                parts.append(
                    f"with {self.counter_constraint} {self.has_counter} counters"
                )
            else:
                parts.append(f"with a {self.has_counter} counter on it")
        return parts

    def _state(self) -> list[str]:
        parts: list[str] = []
        flags = (
            ("attacking", "not attacking", self.attacking),
            ("blocking", "not blocking", self.blocking),
            ("blocked", "unblocked", self.blocked),
            ("tapped", "untapped", self.tapped),
            ("face down", "face up", self.face_down),
            ("token", "nontoken", self.is_token),
            ("commander", "noncommander", self.is_commander),
            ("phased out", "phased in", self.phased_out),
            (
                "that entered this turn",
                "that didn't enter this turn",
                self.entered_this_turn,
            ),
            ("summoning sick", "not summoning sick", self.summoning_sick),
        )
        for yes, no, value in flags:
            if value is True:
                parts.append(yes)
            elif value is False:
                parts.append(no)
        if self.has_keyword:
            parts.append("with " + " and ".join(self.has_keyword))
        if self.lacks_keyword:
            parts.append("without " + " or ".join(self.lacks_keyword))
        return parts

    def _attachments(self) -> list[str]:
        parts: list[str] = []
        if self.attached_to is not None:
            parts.append(f"attached to {self.attached_to.describe()}")
        if self.has_attached is not None:
            parts.append(f"with {self.has_attached.describe()} attached")
        return parts

    def _zone_phrase(self) -> list[str]:
        """Only when it is not the battlefield, which is the default."""
        if self.from_top:
            where = "library"
            if self.owner is ControllerRelation.YOU:
                where = "your library"
            elif self.owner is ControllerRelation.OPPONENT:
                where = "an opponent's library"
            return [f"from the top {self.from_top} of {where}"]
        if self.zones == frozenset({Zone.BATTLEFIELD}):
            return []
        names = sorted(z.name.lower().replace("_", " ") for z in self.zones)
        return ["in " + " or ".join(names)] if names else []

    def __str__(self) -> str:
        return self.describe()


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


class ConditionKind(IntEnum):
    """A boolean test over the game state.

    Used by intervening-if clauses (CR 603.4), by "if" and "unless" in effects,
    and by "as long as" in static abilities. One representation for all three,
    because they are genuinely the same question asked at different times.
    """

    ALWAYS = 0
    NEVER = 1
    #: At least/exactly/at most N objects match a filter.
    OBJECT_COUNT = 2
    #: A player-scoped numeric test, e.g. "if you have 5 or less life".
    LIFE = 3
    CARDS_IN_HAND = 4
    #: Threshold, metalcraft, delirium and friends are all OBJECT_COUNT, but
    #: these earn their own kinds because they read from specific zones.
    CONTROLS_MATCHING = 5
    #: "if it's your turn", "during combat".
    IS_YOUR_TURN = 6
    IS_MAIN_PHASE = 7
    #: Something happened earlier this turn.
    EVENT_THIS_TURN = 8
    #: CR 716.2a: "as long as this Class is level N or greater".
    CLASS_LEVEL = 9
    #: CR 719.3c: "Solved - ..." functions only while the Case is solved.
    IS_SOLVED = 10
    #: CR 702.178a: "Max speed - ..." functions only while the ability's
    #: controller is at maximum speed. A condition rather than an ability word,
    #: because unlike Landfall it genuinely gates whether the ability works.
    AT_MAX_SPEED = 16
    #: "if it's blue", "if it was a creature", "if its power is 3 or greater"
    #: - a test on whatever the resolution last acted on. Needs the resolution
    #: rather than the board, which is why ``holds`` takes the remembered
    #: objects: nothing else in the game state knows what "it" means.
    REMEMBERED_MATCHES = 18
    #: How many players match a filter - "if you have two or more opponents".
    #: A question about the table rather than the board, which none of the
    #: object-count kinds can ask.
    PLAYER_COUNT = 17
    #: "activate only during your upkeep" (CR 702.57a forecast) and the like.
    #: The step is carried in ``constraint`` as the Step's integer value.
    IS_STEP = 13
    #: "if you cast it" - whether this permanent arrived as a spell rather
    #: than being put onto the battlefield. Distinct from NO_MANA_SPENT,
    #: which is about *how* it was paid for.
    WAS_CAST = 23
    #: CR 202.3 and 601.2g: whether any mana was spent casting this spell.
    #: Free spells - cascade, "without paying its mana cost", zero-cost casts
    #: - are what this exists to catch.
    NO_MANA_SPENT = 15
    #: CR 702.33: whether this spell's optional additional cost was paid.
    #: Answered from the spell object, because "kicked" is a fact about how
    #: this particular spell was cast rather than about the board.
    WAS_KICKED = 14
    #: "as long as this permanent has N or more [kind] counters on it" - the
    #: shape shared by level bands (711.2a) and station symbols (721.2a).
    #: Distinct from an ObjectFilter's counter constraint because it asks about
    #: one known object rather than searching for objects that satisfy it.
    COUNTER_COUNT = 11
    #: "an opponent controls more lands than you" - a count for one player
    #: measured against a count for another. Asked per player and true if any
    #: one of them satisfies it: totalling every opponent's permanents
    #: together answers a different and much easier question, which in a
    #: four-player game is true nearly always.
    COMPARE_COUNTS = 19
    #: Boolean composition, so arbitrarily complex clauses stay expressible.
    AND = 20
    OR = 21
    NOT = 22
    #: The parser could not read the clause. A condition that is never true is
    #: the safe failure: the ability simply does not apply.
    UNPARSED = 99


@dataclass(frozen=True, slots=True)
class Condition:
    kind: ConditionKind = ConditionKind.ALWAYS
    filter: ObjectFilter | None = None
    players: PlayerFilter | None = None
    constraint: NumericConstraint | None = None
    counter_type: str = ""
    #: For EVENT_THIS_TURN: which events count. A tuple because one condition
    #: may watch several ("cast or copied a spell this turn"), stored as plain
    #: ints so this module need not import the event enum.
    event_kinds: tuple[int, ...] = ()
    operands: tuple[Condition, ...] = ()
    text: str = ""

    @property
    def is_always(self) -> bool:
        return self.kind is ConditionKind.ALWAYS

    @property
    def is_unparsed(self) -> bool:
        """Whether any part of this condition was left unread.

        An ``UNPARSED`` condition never holds, so the effect it guards never
        happens - which is the safe direction, and exactly why it needs
        reporting. An ability gated by a condition nobody could read does
        nothing at all, and without this it was counted as understood: Ward
        creatures parsed perfectly and never warded anything.

        Recursive, because ``NOT``, ``AND`` and ``OR`` carry their operands
        underneath and an unreadable one buried in a conjunction is as inert
        as an unreadable one on top.
        """
        if self.kind is ConditionKind.UNPARSED:
            return True
        return any(operand.is_unparsed for operand in self.operands)

    def __str__(self) -> str:
        """Describe the condition from its fields, never from ``text``.

        ``text`` is a placeholder the clause writes for debugging - "you
        control ..." with the constraint literally elided. Rendering it made
        the ability explainer say "if you control ..." for a card that reads
        "as long as you control a Teferi planeswalker", which hides exactly
        the thing a reviewer is checking: whether the filter survived the
        parse. Same rule as Effect - the data is the output.
        """
        kind = self.kind

        if kind is ConditionKind.ALWAYS:
            return "always"
        if kind is ConditionKind.NEVER:
            return "never"
        if kind is ConditionKind.NOT:
            inner = ", ".join(str(c) for c in self.operands)
            return f"not ({inner})"
        if kind is ConditionKind.AND:
            return " and ".join(str(c) for c in self.operands)
        if kind is ConditionKind.OR:
            return " or ".join(str(c) for c in self.operands)

        if kind in (ConditionKind.CONTROLS_MATCHING, ConditionKind.OBJECT_COUNT):
            verb = (
                "you control"
                if kind is ConditionKind.CONTROLS_MATCHING
                else "there are"
            )
            if self.filter is None:
                return f"{verb} something"
            # The condition already says who controls them and how many, so
            # the filter should not repeat either. "you control 1 or greater
            # all Teferi planeswalker you control" is honest but unreadable,
            # and an explanation nobody can follow is not much better than a
            # missing one.
            spec = self.filter
            if kind is ConditionKind.CONTROLS_MATCHING:
                spec = replace(spec, controller=ControllerRelation.ANY)
            what = spec.describe(quantified=False)
            if self.constraint is None:
                return f"{verb} {what}"
            return f"{verb} {_count_phrase(self.constraint)} {what}"

        if kind is ConditionKind.PLAYER_COUNT:
            who = str(self.players) if self.players is not None else "players"
            return f"you have {self.constraint} {who}"
        if kind is ConditionKind.AT_MAX_SPEED:
            return "you are at max speed"
        if kind is ConditionKind.IS_YOUR_TURN:
            return "it is your turn"
        if kind is ConditionKind.IS_MAIN_PHASE:
            return "it is a main phase"
        if kind is ConditionKind.NO_MANA_SPENT:
            return "no mana was spent casting it"
        if kind is ConditionKind.WAS_KICKED:
            return "it was kicked"
        if kind is ConditionKind.IS_SOLVED:
            return "this case is solved"
        if kind is ConditionKind.CLASS_LEVEL:
            return f"this class is level {self.constraint}"
        if kind is ConditionKind.COUNTER_COUNT:
            return f"it has {self.constraint} {self.counter_type or 'counters'}"
        if kind in (ConditionKind.LIFE, ConditionKind.CARDS_IN_HAND):
            who = str(self.players) if self.players is not None else "you"
            what = "life" if kind is ConditionKind.LIFE else "cards in hand"
            return f"{who} has {self.constraint} {what}"
        if kind is ConditionKind.UNPARSED:
            return "(a condition that was not understood)"

        # Named rather than guessed at, so a missing case reads as missing.
        described = self.kind.name.lower().replace("_", " ")
        return f"{described}{f' {self.constraint}' if self.constraint else ''}"


def _count_phrase(constraint: NumericConstraint) -> str:
    """"at least one", "exactly two" - a count read as a quantity.

    ``NumericConstraint`` prints as oracle text ("3 or greater"), which reads
    correctly after a noun and badly before one.
    """
    value = str(constraint.value)
    return {
        Comparison.GE: f"at least {value}",
        Comparison.LE: f"at most {value}",
        Comparison.EQ: f"exactly {value}",
        Comparison.NE: f"other than {value}",
        Comparison.GT: f"more than {value}",
        Comparison.LT: f"fewer than {value}",
    }[constraint.comparison]


ALWAYS = Condition(ConditionKind.ALWAYS)
NEVER = Condition(ConditionKind.NEVER)


def creatures(controller: ControllerRelation = ControllerRelation.ANY) -> ObjectFilter:
    """Shorthand used constantly by tests and hand-written abilities."""
    return ObjectFilter(types_all=CardType.CREATURE, controller=controller)


def permanents(controller: ControllerRelation = ControllerRelation.ANY) -> ObjectFilter:
    from .enums import PERMANENT_TYPES

    return ObjectFilter(types_any=PERMANENT_TYPES, controller=controller)


#: "any target" (CR 115.4): a creature, player, planeswalker, or battle.
ANY_TARGET = ObjectFilter(
    types_any=CardType.CREATURE | CardType.PLANESWALKER | CardType.BATTLE,
    zones=frozenset({Zone.BATTLEFIELD}),
)
