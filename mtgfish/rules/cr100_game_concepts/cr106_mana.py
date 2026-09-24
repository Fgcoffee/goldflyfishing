"""Mana symbols, costs, pools, and payment (CR 106, CR 107.4, CR 202, CR 601.2f-h).

Three separate concerns live here, in dependency order:

``ManaSymbol``  A single ``{...}`` symbol, generalised so that hybrid,
                monocolored hybrid, Phyrexian, snow, and colorless symbols are
                all the same shape rather than a pile of special cases.
``ManaCost``    An ordered sequence of symbols, plus the CR 202.2/202.3 derived
                characteristics (colors, mana value) and the CR 601.2f cost
                arithmetic.
``ManaPool``    Mana a player has available, bucketed by type and by any
                restriction attached to it (CR 106.6).

Payment is a real constraint solve, not a greedy match. ``{W/U}{W/U}`` paid
from a pool holding one white and one blue must succeed, and a greedy assigner
that spends both symbols' first choice would wrongly fail it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Iterator, Protocol

from ..kernel.enums import LETTER_TO_COLOR, Color, color_letters
from .cr107_numbers import chosen_number

_SYMBOL_RE = re.compile(r"\{([^}]*)\}")


class UnknownManaSymbol(ValueError):
    """Raised when a ``{...}`` symbol is not recognised.

    Deliberately fatal rather than skipped: a new set introducing a new symbol
    must surface immediately, not silently produce a card that costs less than
    it should.
    """


class ManaSymbolKind(IntEnum):
    """Every mana symbol CR 107.4 lists, grouped by how it is paid.

    The rule names the symbols one by one; this names the *kinds*, because the
    ten hybrid symbols differ only in which two colors they carry and giving
    each its own case would be thirty branches saying the same thing.
    """

    GENERIC = 0  # CR 107.4b: {1}, {2}, ... - payable with any type of mana
    VARIABLE = 1  # CR 107.4b: {X}, {Y}, {Z} - generic once chosen
    COLORED = 2  # CR 107.4a: {W}, payable only with white mana
    COLORLESS = 3  # CR 107.4c: {C} - colorless mana, which is not generic
    SNOW = 4  # {S} - any mana from a snow source (CR 107.4h)
    HYBRID = 5  # CR 107.4e: {W/U}, and {C/W}-style colorless hybrids
    MONOCOLOR_HYBRID = 6  # CR 107.4e: {2/W} - two generic or one white
    PHYREXIAN = 7  # CR 107.4f: {W/P}, {G/W/P} - a color, or 2 life
    HALF = 8  # {H W}, un-set only
    INFINITY = 9  # un-set only


@dataclass(frozen=True, slots=True)
class ManaSymbol:
    """One mana symbol.

    ``colors`` holds every color that can pay this symbol: one bit for ``{W}``,
    two for ``{W/U}``, and for Phyrexian symbols the color(s) that can pay it
    alongside the life option.
    """

    kind: ManaSymbolKind
    colors: Color = Color.NONE
    generic: int = 0
    text: str = ""

    # -- payment predicates -------------------------------------------------

    @property
    def is_generic(self) -> bool:
        """True for symbols that contribute to the undifferentiated generic lump."""
        return self.kind in (ManaSymbolKind.GENERIC, ManaSymbolKind.VARIABLE)

    @property
    def life_cost(self) -> int:
        """Life payable in place of mana, per CR 107.4f. 0 if not Phyrexian."""
        return 2 if self.kind is ManaSymbolKind.PHYREXIAN else 0

    @property
    def is_choice(self) -> bool:
        """True when the payer picks between materially different payments.

        Monocolored hybrid (generic vs colored) and Phyrexian (mana vs life)
        both branch. Ordinary hybrid does not: it is one mana either way, so the
        matcher handles it without branching.
        """
        return self.kind in (ManaSymbolKind.MONOCOLOR_HYBRID, ManaSymbolKind.PHYREXIAN)

    def accepts(self, kind: ManaKind) -> bool:
        """Whether one unit of ``kind`` can pay this symbol as a mana payment.

        This is where CR 107.4a-c and 107.4e are actually enforced: a colored
        symbol takes only its own color, a hybrid takes either of its halves,
        {C} takes colorless mana and nothing else, and a generic symbol takes
        any type at all.
        """
        k = self.kind
        if k is ManaSymbolKind.COLORED or k is ManaSymbolKind.HYBRID:
            if self.colors is Color.NONE:
                # A purely colorless hybrid such as {C/W} with the C side taken.
                return kind.color is Color.NONE
            return kind.color is not Color.NONE and bool(kind.color & self.colors)
        if k is ManaSymbolKind.PHYREXIAN or k is ManaSymbolKind.MONOCOLOR_HYBRID:
            return kind.color is not Color.NONE and bool(kind.color & self.colors)
        if k is ManaSymbolKind.COLORLESS:
            return kind.color is Color.NONE
        if k is ManaSymbolKind.SNOW:
            # {S} constrains the *source*, not the type: any mana produced by a
            # snow permanent pays it (CR 107.4h).
            return kind.snow
        if self.is_generic:
            return True
        return False

    # -- derived characteristics -------------------------------------------

    @property
    def mana_value(self) -> int:
        """This symbol's contribution to mana value (CR 202.3).

        ``{X}`` counts as zero everywhere except on the stack (CR 202.3b), where
        the caller substitutes the chosen value. Hybrid counts as one. A
        monocolored hybrid counts as its generic half, being the larger
        component. Phyrexian counts as one.
        """
        k = self.kind
        if k is ManaSymbolKind.GENERIC:
            return self.generic
        if k is ManaSymbolKind.VARIABLE:
            return 0
        if k is ManaSymbolKind.MONOCOLOR_HYBRID:
            return self.generic
        if k is ManaSymbolKind.INFINITY:
            return 1_000_000
        if k is ManaSymbolKind.HALF:
            # Un-set only; those cards never reach a Commander pool. CR 107.1:
            # the game uses only integers, so rounding up here keeps mana value
            # an int for the whole engine rather than leaking a fraction.
            return 1
        return 1

    @property
    def color_contribution(self) -> Color:
        """Colors this symbol gives the object it appears on (CR 202.2).

        ``{C}`` and ``{S}`` contribute nothing - a card costing only those is
        colorless. Phyrexian and monocolored hybrid symbols *do* contribute
        their color even though they can be paid another way.
        """
        if self.kind in (
            ManaSymbolKind.COLORED,
            ManaSymbolKind.HYBRID,
            ManaSymbolKind.MONOCOLOR_HYBRID,
            ManaSymbolKind.PHYREXIAN,
        ):
            return self.colors
        return Color.NONE

    def __str__(self) -> str:
        return self.text or "{?}"


def parse_mana_symbol(body: str) -> ManaSymbol:
    """Parse the inside of one ``{...}`` mana symbol.

    Raises ``UnknownManaSymbol`` for anything unrecognised, including the
    non-mana symbols ({T}, {Q}, {E}, loyalty) that appear in ability costs -
    those belong to the cost parser, not here.
    """
    raw = body.strip()
    text = "{" + raw + "}"
    upper = raw.upper()

    if upper.isdigit():
        return ManaSymbol(ManaSymbolKind.GENERIC, generic=int(upper), text=text)
    if upper in ("X", "Y", "Z"):
        return ManaSymbol(ManaSymbolKind.VARIABLE, text=text)
    if upper in LETTER_TO_COLOR:
        return ManaSymbol(ManaSymbolKind.COLORED, colors=LETTER_TO_COLOR[upper], text=text)
    if upper == "C":
        return ManaSymbol(ManaSymbolKind.COLORLESS, text=text)
    if upper == "S":
        return ManaSymbol(ManaSymbolKind.SNOW, text=text)
    if raw in ("∞", "INF"):
        return ManaSymbol(ManaSymbolKind.INFINITY, text=text)
    if raw == "½" or (upper.startswith("H") and len(upper) == 2):
        colors = LETTER_TO_COLOR.get(upper[1:], Color.NONE) if len(upper) == 2 else Color.NONE
        return ManaSymbol(ManaSymbolKind.HALF, colors=colors, text=text)

    if "/" in upper:
        parts = upper.split("/")
        phyrexian = parts[-1] == "P"
        if phyrexian:
            parts = parts[:-1]

        colors = Color.NONE
        generic = 0
        saw_colorless = False
        for part in parts:
            if part.isdigit():
                generic = int(part)
            elif part == "C":
                saw_colorless = True
            elif part in LETTER_TO_COLOR:
                colors |= LETTER_TO_COLOR[part]
            else:
                raise UnknownManaSymbol(f"unrecognised component {part!r} in {text}")

        if phyrexian:
            return ManaSymbol(ManaSymbolKind.PHYREXIAN, colors=colors, text=text)
        if generic:
            return ManaSymbol(
                ManaSymbolKind.MONOCOLOR_HYBRID, colors=colors, generic=generic, text=text
            )
        if saw_colorless and colors is Color.NONE:
            return ManaSymbol(ManaSymbolKind.COLORLESS, text=text)
        return ManaSymbol(ManaSymbolKind.HYBRID, colors=colors, text=text)

    raise UnknownManaSymbol(f"not a mana symbol: {text}")


# ---------------------------------------------------------------------------
# Mana costs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManaCost:
    """An ordered sequence of mana symbols.

    Immutable. Cost modification produces a new ManaCost rather than mutating,
    because the unmodified cost stays visible on the card and several effects
    care about the printed value.
    """

    symbols: tuple[ManaSymbol, ...] = ()

    # -- construction -------------------------------------------------------

    @classmethod
    def parse(cls, text: str | None) -> ManaCost:
        """Parse a Scryfall ``mana_cost`` string such as ``"{2}{W}{W}"``.

        An empty or absent cost is *not* the same as a cost of zero: lands have
        no mana cost at all (CR 202.1), which the caller distinguishes by
        checking ``has_cost`` on the card, not by inspecting this object.
        """
        if not text:
            return cls(())
        return cls(tuple(parse_mana_symbol(m.group(1)) for m in _SYMBOL_RE.finditer(text)))

    @classmethod
    def generic(cls, amount: int) -> ManaCost:
        # CR 107.1b: an amount worked out by an effect never goes below zero,
        # and CR 107.4d makes zero generic mana {0} - a cost with no symbols
        # at all, payable with no resources.
        if amount <= 0:
            return cls(())
        return cls((ManaSymbol(ManaSymbolKind.GENERIC, generic=amount, text=f"{{{amount}}}"),))

    # -- derived characteristics -------------------------------------------

    @property
    def mana_value(self) -> int:
        """CR 202.3. ``{X}`` contributes 0; see ``mana_value_with_x``."""
        return sum(s.mana_value for s in self.symbols)

    def mana_value_with_x(self, x: int) -> int:
        """Mana value while on the stack, where X is the chosen value (CR 202.3b)."""
        return self.mana_value + x * self.variable_count

    @property
    def colors(self) -> Color:
        """CR 202.2: the colors of the colored mana symbols in the cost."""
        result = Color.NONE
        for s in self.symbols:
            result |= s.color_contribution
        return result

    @property
    def variable_count(self) -> int:
        """How many ``{X}``-family symbols appear. Nearly always 0 or 1."""
        return sum(1 for s in self.symbols if s.kind is ManaSymbolKind.VARIABLE)

    @property
    def generic_amount(self) -> int:
        """Total fixed generic mana required, ignoring ``{X}``."""
        return sum(s.generic for s in self.symbols if s.kind is ManaSymbolKind.GENERIC)

    @property
    def is_empty(self) -> bool:
        return not self.symbols

    def __bool__(self) -> bool:
        return bool(self.symbols)

    def __iter__(self) -> Iterator[ManaSymbol]:
        return iter(self.symbols)

    def __len__(self) -> int:
        return len(self.symbols)

    def __str__(self) -> str:
        return "".join(s.text for s in self.symbols)

    # -- CR 601.2f cost arithmetic -----------------------------------------

    def plus(self, other: ManaCost) -> ManaCost:
        """Add an additional cost or cost increase."""
        return ManaCost(self.symbols + other.symbols)

    def increased_by(self, amount: int) -> ManaCost:
        """Add ``amount`` generic mana (a cost increase, CR 601.2f)."""
        if amount <= 0:
            return self
        return ManaCost(self.symbols + ManaCost.generic(amount).symbols)

    def reduced_by(self, amount: int) -> ManaCost:
        """Reduce the generic portion by ``amount``, floored at zero.

        Cost reduction never touches colored mana requirements, nor hybrid,
        monocolored hybrid, or Phyrexian symbols (CR 601.2f). ``{X}`` must
        already have been substituted before calling this, since X becomes
        generic mana once chosen.
        """
        if amount <= 0:
            return self

        remaining = amount
        out: list[ManaSymbol] = []
        for s in self.symbols:
            if remaining and s.kind is ManaSymbolKind.GENERIC:
                take = min(remaining, s.generic)
                remaining -= take
                left = s.generic - take
                if left:
                    out.append(
                        ManaSymbol(ManaSymbolKind.GENERIC, generic=left, text=f"{{{left}}}")
                    )
            else:
                out.append(s)
        return ManaCost(tuple(out))

    def reduced_by_mana(self, reduction: ManaCost) -> ManaCost:
        """Reduce this cost by specific mana (CR 118.7b-g).

        Reduction by *colored* mana is not the same as reduction by generic.
        CR 118.7c: a reduction of {B} against a cost with no black component
        reduces one generic instead, and a reduction that exceeds the coloured
        component spills the difference into generic. Treating every reduction
        as generic makes Bloodstained Mire-style effects too weak, and treating
        them all as coloured makes them too strong.
        """
        remaining = list(self.symbols)
        generic_reduction = 0

        for symbol in reduction.symbols:
            kind = symbol.kind
            if kind is ManaSymbolKind.GENERIC:
                generic_reduction += symbol.generic
                continue
            if kind in (ManaSymbolKind.SNOW, ManaSymbolKind.VARIABLE):
                # CR 118.7g: snow reduces generic mana.
                generic_reduction += 1
                continue

            wanted = symbol.colors if kind is not ManaSymbolKind.COLORLESS else Color.NONE
            index = _find_matching(remaining, kind, wanted)
            if index is None:
                # CR 118.7b/c/d: nothing of that type to reduce, so it comes
                # off the generic component instead.
                generic_reduction += 1
            else:
                remaining.pop(index)

        return ManaCost(tuple(remaining)).reduced_by(generic_reduction)

    def substitute_x(self, x: int) -> ManaCost:
        """Replace every ``{X}`` with that much generic mana (CR 601.2b then 601.2f).

        CR 107.1b: the value chosen for X is zero or more. A negative choice is
        not a cost reduction, and an X of zero leaves no symbol behind rather
        than a ``{0}`` - which is the same cost either way (CR 107.4d).
        """
        if not self.variable_count:
            return self
        x = chosen_number(x)
        out: list[ManaSymbol] = []
        for s in self.symbols:
            if s.kind is ManaSymbolKind.VARIABLE:
                if x > 0:
                    out.append(ManaSymbol(ManaSymbolKind.GENERIC, generic=x, text=f"{{{x}}}"))
            else:
                out.append(s)
        return ManaCost(tuple(out))


def _find_matching(
    symbols: list[ManaSymbol], kind: ManaSymbolKind, colors: Color
) -> int | None:
    """Index of a symbol a coloured or colorless reduction can come off.

    Prefers an exact colour match over a hybrid, because using up a hybrid that
    could have been paid another way is strictly worse for the payer and CR
    118.7e leaves the choice to them.
    """
    if kind is ManaSymbolKind.COLORLESS:
        for index, symbol in enumerate(symbols):
            if symbol.kind is ManaSymbolKind.COLORLESS:
                return index
        return None

    for wanted_kind in (
        ManaSymbolKind.COLORED,
        ManaSymbolKind.PHYREXIAN,
        ManaSymbolKind.HYBRID,
        ManaSymbolKind.MONOCOLOR_HYBRID,
    ):
        for index, symbol in enumerate(symbols):
            if symbol.kind is wanted_kind and (symbol.colors & colors):
                return index
    return None


#: CR 107.4d: {0}, a cost payable with no resources. Distinct from having no
#: mana cost at all, which CR 118.6 makes *unpayable* rather than free.
ZERO_COST = ManaCost(())


# ---------------------------------------------------------------------------
# Mana pools
# ---------------------------------------------------------------------------


class ManaRestriction(Protocol):
    """A rider limiting what a unit of mana may be spent on (CR 106.6).

    Restrictions are all-or-nothing for a given payment - "spend only on
    creature spells" is a question about the spell being cast, not about which
    symbol the mana pays - so the solver filters unusable buckets up front.
    """

    key: str

    def permits(self, context: object) -> bool: ...


@dataclass(frozen=True, slots=True)
class SpendOnlyOn:
    """"Spend this mana only to cast creature spells." (CR 106.6).

    The payment solver already filters unusable buckets by asking
    ``permits`` - this is simply the first concrete restriction to exist, so
    until now every "spend only on" rider was dropped and the mana was
    general-purpose. Ramp that is supposed to be narrow being spendable on
    anything is a straightforward overstatement of a deck's speed.

    A restriction with no filter permits nothing rather than everything: an
    unreadable rider must not quietly widen the mana.
    """

    key: str
    filter: object | None = None

    def permits(self, context: object) -> bool:
        if self.filter is None:
            return False
        if context is None:
            # Nothing is being paid for yet - a bare payability check. Say yes
            # so the mana still counts as available; the real check happens
            # when there is a spell to test.
            return True

        # Tested against the object's *printed* types rather than through the
        # layer system, because a payment context is a bare GameObject with no
        # game attached. A spell's card types practically never change while
        # it is on the stack, and the alternative - threading the game through
        # every payment call - would buy nothing for that.
        card = getattr(context, "card", None)
        if card is None:
            return True  # A token or emblem is not a spell being cast.
        faces = getattr(card, "faces", ())
        index = getattr(context, "face_index", 0)
        if index >= len(faces):
            return True
        type_line = getattr(faces[index], "type_line", None)
        if type_line is None:
            return True

        wanted = self.filter.types_all | self.filter.types_any
        if wanted and not (type_line.types & wanted):
            return False
        subtypes = self.filter.subtypes_all + self.filter.subtypes_any
        if subtypes and not (set(subtypes) & set(type_line.subtypes)):
            return False
        return True


@dataclass(frozen=True, slots=True)
class ManaKind:
    """The type of one unit of mana, plus anything that constrains its use.

    ``color is Color.NONE`` means colorless mana, which is a real mana type
    (CR 106.1b) and is not the same as generic mana (a cost, not a type).
    """

    color: Color = Color.NONE
    snow: bool = False
    restriction: ManaRestriction | None = None

    def __str__(self) -> str:
        base = color_letters(self.color) if self.color else "C"
        if self.snow:
            base += "(snow)"
        if self.restriction is not None:
            base += f"[{self.restriction.key}]"
        return base


#: Sort key giving a stable, colour-canonical bucket order. Payment must be
#: deterministic across runs for replay to reconstruct a game from its seed.
def _kind_sort_key(kind: ManaKind) -> tuple:
    return (int(kind.color), kind.snow, kind.restriction.key if kind.restriction else "")


@dataclass(slots=True)
class ManaPool:
    """Mana currently available to a player.

    Empties at the end of each step and phase (CR 500.4). Bucketed by kind
    rather than stored as a list, because a pool of eight identical green mana
    should be one entry, not eight.
    """

    buckets: dict[ManaKind, int] = field(default_factory=dict)

    def add(self, kind: ManaKind, amount: int = 1) -> None:
        if amount <= 0:
            return
        self.buckets[kind] = self.buckets.get(kind, 0) + amount

    def remove(self, kind: ManaKind, amount: int = 1) -> None:
        have = self.buckets.get(kind, 0)
        if have < amount:
            raise ValueError(f"cannot remove {amount} {kind} from pool holding {have}")
        if have == amount:
            del self.buckets[kind]
        else:
            self.buckets[kind] = have - amount

    @property
    def total(self) -> int:
        return sum(self.buckets.values())

    def usable_for(self, context: object) -> int:
        """How much of this pool could be spent on ``context``.

        ``total`` counts restricted mana that cannot legally pay for the thing
        being considered, which turns a pool of narrow ramp into a pool of
        general mana wherever an upper bound is taken.
        """
        return sum(
            n
            for kind, n in self.buckets.items()
            if kind.restriction is None or kind.restriction.permits(context)
        )

    def amount_of(self, color: Color) -> int:
        return sum(n for k, n in self.buckets.items() if k.color == color)

    def clear(self) -> int:
        """Empty the pool, returning how much was lost (CR 500.4).

        Mana emptying is not a cost or a payment; unspent mana simply ceases to
        exist at the end of each step and phase.
        """
        lost = self.total
        self.buckets.clear()
        return lost

    def copy(self) -> ManaPool:
        return ManaPool(dict(self.buckets))

    def snapshot(self) -> tuple[tuple[ManaKind, int], ...]:
        """Deterministically-ordered contents, for logging and hashing."""
        return tuple(sorted(self.buckets.items(), key=lambda kv: _kind_sort_key(kv[0])))

    def __str__(self) -> str:
        if not self.buckets:
            return "(empty)"
        return " ".join(f"{n}x{k}" for k, n in self.snapshot())


# ---------------------------------------------------------------------------
# Payment solving
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Payment:
    """A concrete, legal way to pay a mana cost."""

    #: How much of each bucket to spend.
    mana: tuple[tuple[ManaKind, int], ...]
    #: Life paid for Phyrexian symbols (CR 107.4f).
    life: int = 0

    @property
    def total_mana(self) -> int:
        return sum(n for _, n in self.mana)


def find_payment(
    pool: ManaPool,
    cost: ManaCost,
    *,
    x_value: int = 0,
    life_available: int = 0,
    context: object = None,
) -> Payment | None:
    """Find a legal payment for ``cost`` from ``pool``, or None if impossible.

    Returns the first legal payment under a deterministic preference order:
    mana before life for Phyrexian symbols, and the colored half before the
    generic half of a monocolored hybrid (spending one mana beats spending two).
    Choosing *well* is the AI's job; this only establishes legality and gives a
    workable default.

    ``context`` is the spell or ability being paid for, used to evaluate mana
    restrictions (CR 106.6).
    """
    if cost.is_empty:
        return Payment(())

    # Mana carrying a restriction that this payment doesn't satisfy is simply
    # unavailable (CR 106.6), so drop those buckets before solving.
    usable: list[tuple[ManaKind, int]] = [
        (k, n)
        for k, n in sorted(pool.buckets.items(), key=lambda kv: _kind_sort_key(kv[0]))
        if k.restriction is None or k.restriction.permits(context)
    ]
    # An empty usable list is not a special case: the solver below correctly
    # reports failure, except for an all-Phyrexian cost which it can still pay
    # entirely with life.
    kinds = [k for k, _ in usable]
    counts = [n for _, n in usable]

    symbols = cost.substitute_x(x_value).symbols
    specific = [s for s in symbols if not s.is_generic]
    generic_needed = sum(s.generic for s in symbols if s.kind is ManaSymbolKind.GENERIC)

    # Symbols offering a genuine choice are resolved by branching. Costs carry
    # only a handful of these, so exhaustive enumeration is cheap and exact
    # where a heuristic would be wrong.
    choice_points = [i for i, s in enumerate(specific) if s.is_choice]
    if len(choice_points) > 12:
        raise ValueError(f"unreasonable number of hybrid symbols in cost {cost}")

    for branch in _iter_branches(len(choice_points)):
        spend = [0] * len(kinds)
        life = 0
        extra_generic = 0
        must_match: list[ManaSymbol] = []

        for idx, sym in enumerate(specific):
            if idx in choice_points:
                # Branch bit 0 = take the "one mana" reading, 1 = the alternative
                # (pay life for Phyrexian, or pay the generic half of {2/W}).
                take_alternative = branch[choice_points.index(idx)]
                if take_alternative:
                    if sym.kind is ManaSymbolKind.PHYREXIAN:
                        life += sym.life_cost
                    else:
                        extra_generic += sym.generic
                    continue
            must_match.append(sym)

        if life > life_available:
            continue

        if not _assign_specific(must_match, kinds, counts, spend):
            continue

        remaining = sum(counts[i] - spend[i] for i in range(len(kinds)))
        need = generic_needed + extra_generic
        if remaining < need:
            continue

        # Generic mana accepts anything still in the pool. Spend from the
        # canonical bucket order so the result is reproducible.
        for i in range(len(kinds)):
            if need <= 0:
                break
            take = min(need, counts[i] - spend[i])
            spend[i] += take
            need -= take

        if need > 0:
            continue

        return Payment(
            mana=tuple((kinds[i], spend[i]) for i in range(len(kinds)) if spend[i]),
            life=life,
        )

    return None


def can_pay(
    pool: ManaPool,
    cost: ManaCost,
    *,
    x_value: int = 0,
    life_available: int = 0,
    context: object = None,
) -> bool:
    return (
        find_payment(
            pool, cost, x_value=x_value, life_available=life_available, context=context
        )
        is not None
    )


def _iter_branches(n: int) -> Iterable[tuple[int, ...]]:
    """Yield choice assignments, all-preferred first.

    Ordering matters: it makes ``find_payment`` deterministic and makes it
    prefer spending one mana over two, and mana over life.
    """
    if n == 0:
        yield ()
        return
    # Sorted by how many alternatives are taken, so the all-preferred branch
    # comes first and life is only paid when mana genuinely cannot cover it.
    for mask in sorted(range(1 << n), key=lambda m: (bin(m).count("1"), m)):
        yield tuple((mask >> i) & 1 for i in range(n))


def _assign_specific(
    symbols: list[ManaSymbol],
    kinds: list[ManaKind],
    counts: list[int],
    spend: list[int],
) -> bool:
    """Assign one unit of mana to each symbol via backtracking.

    Most-constrained-first ordering: a symbol matching only one bucket is
    resolved before one matching four, which prunes the search hard. This is
    what makes ``{W/U}{W/U}`` against one white and one blue succeed where a
    greedy left-to-right assigner fails.
    """
    if not symbols:
        return True

    candidates: list[tuple[int, list[int]]] = []
    for si, sym in enumerate(symbols):
        matches = [ki for ki, k in enumerate(kinds) if sym.accepts(k)]
        if not matches:
            return False
        candidates.append((si, matches))
    candidates.sort(key=lambda c: (len(c[1]), c[0]))
    order = [si for si, _ in candidates]
    match_map = {si: m for si, m in candidates}

    def recurse(pos: int) -> bool:
        if pos == len(order):
            return True
        si = order[pos]
        for ki in match_map[si]:
            if counts[ki] - spend[ki] <= 0:
                continue
            spend[ki] += 1
            if recurse(pos + 1):
                return True
            spend[ki] -= 1
        return False

    return recurse(0)
