"""Mana symbol, cost, and payment tests (CR 106, 107.4, 202, 601.2f).

Cases are named after real cards wherever possible, so a failure points at
something checkable against a physical card rather than an abstraction.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.enums import Color
from mtgfish.rules.mana import (
    ManaCost,
    ManaKind,
    ManaPool,
    ManaSymbolKind,
    UnknownManaSymbol,
    can_pay,
    find_payment,
    parse_mana_symbol,
)

W = ManaKind(Color.WHITE)
U = ManaKind(Color.BLUE)
B = ManaKind(Color.BLACK)
R = ManaKind(Color.RED)
G = ManaKind(Color.GREEN)
C = ManaKind(Color.NONE)
SNOW_G = ManaKind(Color.GREEN, snow=True)


def pool(**kw: int) -> ManaPool:
    """Build a pool from letters, e.g. ``pool(W=2, U=1)``."""
    lookup = {"W": W, "U": U, "B": B, "R": R, "G": G, "C": C, "SG": SNOW_G}
    p = ManaPool()
    for letter, count in kw.items():
        p.add(lookup[letter], count)
    return p


# ---------------------------------------------------------------------------
# Parsing and derived characteristics (CR 202.2, 202.3)
# ---------------------------------------------------------------------------


def test_plain_cost():
    cost = ManaCost.parse("{2}{W}{W}")  # Wrath of God
    assert cost.mana_value == 4
    assert cost.colors == Color.WHITE
    assert cost.generic_amount == 2
    assert str(cost) == "{2}{W}{W}"


def test_sol_ring_is_colorless():
    cost = ManaCost.parse("{1}")
    assert cost.mana_value == 1
    assert cost.colors == Color.NONE


def test_no_mana_cost_is_empty():
    """Lands have no mana cost at all (CR 202.1), which is not a cost of zero."""
    assert ManaCost.parse(None).is_empty
    assert ManaCost.parse("").is_empty
    assert ManaCost.parse("{0}").mana_value == 0
    assert not ManaCost.parse("{0}").is_empty


def test_hybrid_counts_as_one_and_is_both_colors():
    cost = ManaCost.parse("{W/U}")  # e.g. Wear // Tear-style hybrid symbol
    assert cost.mana_value == 1
    assert cost.colors == Color.WHITE | Color.BLUE


def test_monocolored_hybrid_uses_larger_component():
    """CR 202.3b: {2/R} contributes 2 to mana value. Flame Javelin is a 6-drop."""
    cost = ManaCost.parse("{2/R}{2/R}{2/R}")
    assert cost.mana_value == 6
    assert cost.colors == Color.RED


def test_phyrexian_is_colored_and_counts_as_one():
    """Gitaxian Probe is blue and has mana value 1 even when paid with life."""
    cost = ManaCost.parse("{U/P}")
    assert cost.mana_value == 1
    assert cost.colors == Color.BLUE
    assert cost.symbols[0].life_cost == 2


def test_hybrid_phyrexian():
    """March of the Machine cycle, e.g. {G/W/P}: green, white, or 2 life."""
    sym = parse_mana_symbol("G/W/P")
    assert sym.kind is ManaSymbolKind.PHYREXIAN
    assert sym.colors == Color.GREEN | Color.WHITE
    assert sym.mana_value == 1


def test_colorless_symbol_is_not_a_color():
    """Warping Wail costs {1}{C} and is colorless (CR 105.1)."""
    cost = ManaCost.parse("{1}{C}")
    assert cost.mana_value == 2
    assert cost.colors == Color.NONE


def test_snow_symbol_contributes_no_color():
    cost = ManaCost.parse("{S}{S}")
    assert cost.mana_value == 2
    assert cost.colors == Color.NONE


def test_x_counts_as_zero_until_chosen():
    cost = ManaCost.parse("{X}{R}")  # Fireball
    assert cost.mana_value == 1
    assert cost.mana_value_with_x(3) == 4
    assert cost.substitute_x(3).mana_value == 4
    assert str(cost.substitute_x(3)) == "{3}{R}"


def test_x_of_zero_leaves_no_generic_symbol():
    assert str(ManaCost.parse("{X}{R}").substitute_x(0)) == "{R}"


def test_unknown_symbol_is_fatal():
    """A new set's new symbol must surface loudly, not silently cost nothing."""
    with pytest.raises(UnknownManaSymbol):
        parse_mana_symbol("QQ")
    with pytest.raises(UnknownManaSymbol):
        parse_mana_symbol("T")  # A cost symbol, but not a *mana* symbol.


# ---------------------------------------------------------------------------
# Cost arithmetic (CR 601.2f)
# ---------------------------------------------------------------------------


def test_reduction_only_touches_generic():
    cost = ManaCost.parse("{4}{G}{G}")
    assert str(cost.reduced_by(2)) == "{2}{G}{G}"


def test_reduction_floors_at_zero_and_never_eats_colored():
    """CR 601.2f: cost reduction can't reduce a colored mana requirement."""
    cost = ManaCost.parse("{2}{U}{U}")
    assert str(cost.reduced_by(10)) == "{U}{U}"
    assert cost.reduced_by(10).mana_value == 2


def test_reduction_does_not_touch_hybrid_or_phyrexian():
    assert str(ManaCost.parse("{2/W}{W/P}").reduced_by(3)) == "{2/W}{W/P}"


def test_increase_then_reduce_order():
    """CR 601.2f applies increases first, then reductions."""
    cost = ManaCost.parse("{1}{B}").increased_by(2).reduced_by(1)
    assert cost.mana_value == 3  # 1 + 2 - 1 generic, plus {B}


# ---------------------------------------------------------------------------
# Payment (CR 601.2h)
# ---------------------------------------------------------------------------


def test_generic_accepts_any_mana():
    assert can_pay(pool(G=2, R=1), ManaCost.parse("{3}"))
    assert not can_pay(pool(G=2), ManaCost.parse("{3}"))


def test_colored_requirement_is_not_satisfied_by_wrong_color():
    assert not can_pay(pool(W=1, U=1), ManaCost.parse("{W}{W}"))
    assert can_pay(pool(W=2), ManaCost.parse("{W}{W}"))


def test_hybrid_pair_needs_real_matching_not_greedy():
    """{W/U}{W/U} from one white and one blue.

    A greedy assigner that gives both symbols their first choice fails here.
    The constraint solve must succeed.
    """
    assert can_pay(pool(W=1, U=1), ManaCost.parse("{W/U}{W/U}"))


def test_flexible_symbol_yields_to_the_rigid_one():
    """{W/U}{W} from one white and one blue.

    Left-to-right assignment spends the white on the hybrid and then strands
    the {W}. Most-constrained-first ordering resolves {W} first and succeeds.
    """
    assert can_pay(pool(W=1, U=1), ManaCost.parse("{W/U}{W}"))
    assert can_pay(pool(W=1, U=1), ManaCost.parse("{W}{W/U}"))


def test_hybrid_pair_from_a_single_color():
    assert can_pay(pool(U=2), ManaCost.parse("{W/U}{W/U}"))
    assert not can_pay(pool(U=1, G=1), ManaCost.parse("{W/U}{W/U}"))


def test_specific_symbols_do_not_steal_mana_the_generic_needs():
    """{1}{G}{G} from exactly G, G, R must work: R covers the generic."""
    assert can_pay(pool(G=2, R=1), ManaCost.parse("{1}{G}{G}"))
    assert not can_pay(pool(G=2), ManaCost.parse("{1}{G}{G}"))


def test_phyrexian_prefers_mana_over_life():
    p = find_payment(pool(U=1), ManaCost.parse("{U/P}"), life_available=40)
    assert p is not None
    assert p.life == 0
    assert p.total_mana == 1


def test_phyrexian_falls_back_to_life():
    p = find_payment(pool(G=1), ManaCost.parse("{U/P}"), life_available=40)
    assert p is not None
    assert p.life == 2
    assert p.total_mana == 0


def test_phyrexian_needs_the_life_to_actually_be_there():
    assert not can_pay(pool(G=1), ManaCost.parse("{U/P}"), life_available=1)


def test_all_phyrexian_cost_payable_from_an_empty_pool():
    """Phyrexian Unlife-style: {U/P}{U/P} with no mana at all, only life."""
    p = find_payment(ManaPool(), ManaCost.parse("{U/P}{U/P}"), life_available=40)
    assert p is not None
    assert p.life == 4
    assert p.total_mana == 0


def test_monocolored_hybrid_prefers_the_cheaper_colored_half():
    p = find_payment(pool(R=1, G=3), ManaCost.parse("{2/R}"))
    assert p is not None
    assert p.total_mana == 1


def test_monocolored_hybrid_falls_back_to_two_generic():
    p = find_payment(pool(G=2), ManaCost.parse("{2/R}"))
    assert p is not None
    assert p.total_mana == 2
    assert not can_pay(pool(G=1), ManaCost.parse("{2/R}"))


def test_snow_requires_a_snow_source():
    """{S} constrains the source, not the color (CR 107.4h)."""
    assert can_pay(pool(SG=1), ManaCost.parse("{S}"))
    assert not can_pay(pool(G=1), ManaCost.parse("{S}"))
    # Snow mana is still green mana and pays {G} perfectly well.
    assert can_pay(pool(SG=1), ManaCost.parse("{G}"))


def test_colorless_symbol_rejects_colored_mana():
    """{C} requires colorless mana specifically (CR 107.4c)."""
    assert can_pay(pool(C=1), ManaCost.parse("{C}"))
    assert not can_pay(pool(G=5), ManaCost.parse("{C}"))
    # ...but colorless mana happily pays a generic cost.
    assert can_pay(pool(C=3), ManaCost.parse("{3}"))


def test_x_value_is_included_in_payment():
    cost = ManaCost.parse("{X}{R}")
    assert can_pay(pool(R=1, G=3), cost, x_value=3)
    assert not can_pay(pool(R=1, G=2), cost, x_value=3)


# ---------------------------------------------------------------------------
# Restricted mana (CR 106.6b)
# ---------------------------------------------------------------------------


class CreaturesOnly:
    key = "creature-spells-only"

    def permits(self, context: object) -> bool:
        return context == "creature"


def test_restricted_mana_is_unavailable_for_the_wrong_spell():
    restricted = ManaKind(Color.GREEN, restriction=CreaturesOnly())
    p = ManaPool()
    p.add(restricted, 3)
    assert can_pay(p, ManaCost.parse("{3}"), context="creature")
    assert not can_pay(p, ManaCost.parse("{3}"), context="instant")


def test_restricted_mana_mixes_with_unrestricted():
    restricted = ManaKind(Color.GREEN, restriction=CreaturesOnly())
    p = pool(R=1)
    p.add(restricted, 1)
    assert can_pay(p, ManaCost.parse("{2}"), context="creature")
    assert not can_pay(p, ManaCost.parse("{2}"), context="instant")


# ---------------------------------------------------------------------------
# Pool mechanics (CR 500.4)
# ---------------------------------------------------------------------------


def test_pool_empties_between_steps():
    p = pool(G=2, R=1)
    assert p.total == 3
    assert p.clear() == 3
    assert p.total == 0


def test_pool_snapshot_is_deterministic():
    a = ManaPool()
    a.add(R, 1)
    a.add(W, 2)
    b = ManaPool()
    b.add(W, 2)
    b.add(R, 1)
    assert a.snapshot() == b.snapshot()
