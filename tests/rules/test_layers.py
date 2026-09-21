"""The layer system (CR 613).

These are the cases that separate a rules engine from a card-flipping
simulator. Every scenario here is a well-known interaction with a published,
checkable answer, and each one is wrong under a naive "apply effects in the
order they happened" implementation.

Card abilities are hand-written here because the parser does not exist yet;
each one is transcribed from the real oracle text quoted in its docstring.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import CardType, Color, Supertype
from mtgfish.rules.query import (
    ControllerRelation,
    ObjectFilter,
    Value,
    ValueKind,
)

# ---------------------------------------------------------------------------
# Hand-written abilities, transcribed from oracle text
# ---------------------------------------------------------------------------

CREATURES_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
)
ALL_CREATURES = ObjectFilter(types_all=CardType.CREATURE)
ALL_LANDS = ObjectFilter(types_all=CardType.LAND)
NONBASIC_LANDS = ObjectFilter(types_all=CardType.LAND, supertypes_none=Supertype.BASIC)


def glorious_anthem() -> Ability:
    """"Creatures you control get +1/+1." - layer 7c."""
    return Ability.static(
        Effect(
            EffectKind.MODIFY_PT,
            targets=CREATURES_YOU_CONTROL,
            amount=Value.of(1),
            amount2=Value.of(1),
        ),
        text="Creatures you control get +1/+1.",
    )


def humility() -> Ability:
    """"All creatures lose all abilities and have base power and toughness 1/1."

    Two effects, in two different layers: ability removal in 6, and setting
    power and toughness in 7b.
    """
    return Ability.static(
        Effect(EffectKind.REMOVE_ABILITIES, targets=ALL_CREATURES),
        Effect(
            EffectKind.SET_PT,
            targets=ALL_CREATURES,
            amount=Value.of(1),
            amount2=Value.of(1),
        ),
        text="All creatures lose all abilities and have base power and toughness 1/1.",
    )


def opalescence() -> Ability:
    """"Each other non-Aura enchantment is a creature in addition to its other
    types and has base power and toughness each equal to its mana value."

    Layer 4 for the type change, 7b for the power and toughness - and the
    "its" refers to the enchantment being animated, not to Opalescence.
    """
    other_enchantments = ObjectFilter(
        types_all=CardType.ENCHANTMENT,
        subtypes_none=("Aura",),
        other_than_source=True,
    )
    mana_value_of_it = Value(ValueKind.MANA_VALUE, of_affected=True)
    return Ability.static(
        Effect(EffectKind.ADD_TYPE, targets=other_enchantments, types=CardType.CREATURE),
        Effect(
            EffectKind.SET_PT,
            targets=other_enchantments,
            amount=mana_value_of_it,
            amount2=mana_value_of_it,
        ),
        text="Each other non-Aura enchantment is a creature ... equal to its mana value.",
    )


def blood_moon() -> Ability:
    """"Nonbasic lands are Mountains." - layer 4, and CR 305.7 strips abilities."""
    return Ability.static(
        Effect(
            EffectKind.SET_TYPE,
            targets=NONBASIC_LANDS,
            types=CardType.LAND,
            keywords=("Mountain",),
        ),
        text="Nonbasic lands are Mountains.",
    )


def urborg() -> Ability:
    """"Each land is a Swamp in addition to its other land types." - layer 4."""
    return Ability.static(
        Effect(EffectKind.ADD_TYPE, targets=ALL_LANDS, keywords=("Swamp",)),
        text="Each land is a Swamp in addition to its other land types.",
    )


def flying_granter() -> Ability:
    return Ability.static(
        Effect(EffectKind.GRANT_ABILITY, targets=CREATURES_YOU_CONTROL, keywords=("Flying",)),
        text="Creatures you control have flying.",
    )


def tarmogoyf_cda() -> Ability:
    """A characteristic-defining ability that sets power and toughness.

    Stands in for Tarmogoyf: a CDA applies in layer 7a, before anything that
    sets or modifies power and toughness.
    """
    ability = Ability.static(
        Effect(EffectKind.SET_PT, amount=Value.of(4), amount2=Value.of(5)),
        text="Base power and toughness 4/5 (CDA).",
    )
    return Ability(
        AbilityKind.STATIC,
        effects=ability.effects,
        text=ability.text,
        is_characteristic_defining=True,
    )


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities(
        {
            "Glorious Anthem": (glorious_anthem(),),
            "Humility": (humility(),),
            "Opalescence": (opalescence(),),
            "Blood Moon": (blood_moon(),),
            "Urborg, Tomb of Yawgmoth": (urborg(),),
            "Levitation": (flying_granter(),),
            "Tarmogoyf": (tarmogoyf_cda(),),
        }
    )


@pytest.fixture
def board(card_db, scripts):
    return make_board(card_db, scripts)


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_printed_power_and_toughness(board):
    assert board.pt(board.play("Grizzly Bears")) == (2, 2)


def test_static_pump_applies(board):
    bears = board.play("Grizzly Bears")
    board.play("Glorious Anthem")
    assert board.pt(bears) == (3, 3)


def test_static_pump_only_affects_its_controller(board):
    mine = board.play("Grizzly Bears", controller=0)
    theirs = board.play("Grizzly Bears", controller=1)
    board.play("Glorious Anthem", controller=0)
    assert board.pt(mine) == (3, 3)
    assert board.pt(theirs) == (2, 2)


def test_effect_stops_when_its_source_leaves(board):
    """CR 611.3: a static ability applies only while the source has it."""
    bears = board.play("Grizzly Bears")
    anthem = board.play("Glorious Anthem")
    assert board.pt(bears) == (3, 3)

    board.game.move_object(anthem, board.game.objects[anthem.id].zone.GRAVEYARD)
    assert board.pt(bears) == (2, 2)


def test_two_anthems_stack(board):
    bears = board.play("Grizzly Bears")
    board.play("Glorious Anthem")
    board.play("Gaea's Anthem")
    board.scripts.add("Gaea's Anthem", glorious_anthem())
    board.refresh()
    assert board.pt(bears) == (4, 4)


# ---------------------------------------------------------------------------
# Layer 7 sublayer order (CR 613.4)
# ---------------------------------------------------------------------------


def test_counters_apply_after_setting_power_and_toughness(board):
    """CR 613.4d: layer 7d comes after 7b.

    A creature set to 1/1 by Humility while carrying three +1/+1 counters is a
    4/4, not a 1/1 - regardless of when the counters arrived.
    """
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 3)
    board.play("Humility")
    assert board.pt(bears) == (4, 4)


def test_setting_power_beats_an_earlier_modification(board):
    """Layer 7b is applied before 7c, whatever the timestamps say."""
    bears = board.play("Grizzly Bears")
    board.play("Glorious Anthem")  # 7c, earlier timestamp
    board.play("Humility")  # 7b, later timestamp
    # 1/1 base from Humility, then +1/+1 from the Anthem.
    assert board.pt(bears) == (2, 2)


def test_setting_power_first_then_modification(board):
    """The reverse timestamp order gives the same answer, which is the point."""
    bears = board.play("Grizzly Bears")
    board.play("Humility")  # 7b, earlier timestamp
    board.play("Glorious Anthem")  # 7c, later timestamp
    assert board.pt(bears) == (2, 2)


def test_minus_counters_annihilate_against_plus(board):
    bears = board.play("Grizzly Bears")
    bears.add_counters("+1/+1", 2)
    bears.add_counters("-1/-1", 1)
    assert board.pt(bears) == (3, 3)


def test_characteristic_defining_ability_applies_in_7a(board):
    """A CDA is applied before anything that sets or modifies P/T."""
    goyf = board.play("Tarmogoyf")
    assert board.pt(goyf) == (4, 5)
    board.play("Glorious Anthem")
    assert board.pt(goyf) == (5, 6)


# ---------------------------------------------------------------------------
# Layer 6: ability removal
# ---------------------------------------------------------------------------


def test_humility_removes_keywords(board):
    flier = board.play("Serra Angel")
    board.scripts.add(
        "Serra Angel", Ability(AbilityKind.STATIC, keyword="Flying", text="Flying")
    )
    board.refresh()
    assert "Flying" in board.keywords(flier)

    board.play("Humility")
    assert board.keywords(flier) == set()


def test_granted_ability_appears(board):
    bears = board.play("Grizzly Bears")
    assert "Flying" not in board.keywords(bears)
    board.play("Levitation")
    assert "Flying" in board.keywords(bears)


def test_humility_removes_an_ability_that_would_grant_in_layer_six(board):
    """Both apply in layer 6, so timestamp decides.

    Levitation later than Humility means Humility strips abilities first and
    Levitation then grants flying back.
    """
    bears = board.play("Grizzly Bears")
    board.play("Humility")
    board.play("Levitation")
    assert "Flying" in board.keywords(bears)


# ---------------------------------------------------------------------------
# Humility + Opalescence (CR 613 worked example)
# ---------------------------------------------------------------------------


def test_opalescence_animates_other_enchantments(board):
    """Each becomes a creature with P/T equal to its own mana value.

    Ghostly Prison costs {2}{W}, so it lands as a 3/3 - and "its" means the
    enchantment being animated, not Opalescence.
    """
    board.play("Opalescence")  # {2}{W}{W}, mana value 4
    prison = board.play("Ghostly Prison")  # {2}{W}, mana value 3
    assert board.game.characteristics(prison).has_type(CardType.CREATURE)
    assert board.pt(prison) == (3, 3)


def test_an_animated_anthem_pumps_itself(board):
    """Glorious Anthem animated by Opalescence becomes a creature you control.

    So its own "creatures you control get +1/+1" now includes itself: 3/3 base
    from Opalescence in layer 7b, then +1/+1 in 7c.
    """
    board.play("Opalescence")
    anthem = board.play("Glorious Anthem")  # {1}{W}{W}, mana value 3
    assert board.pt(anthem) == (4, 4)


def test_opalescence_does_not_animate_itself(board):
    """"Each *other* non-Aura enchantment"."""
    opal = board.play("Opalescence")
    assert not board.game.characteristics(opal).has_type(CardType.CREATURE)


def test_a_non_creature_permanent_has_no_power_or_toughness(board):
    """CR 208.3. Defaulting to 0/0 would feed it to the 0-toughness SBA."""
    assert board.pt(board.play("Opalescence")) == (None, None)


@pytest.mark.parametrize("opalescence_first", [True, False])
def test_humility_and_opalescence(board, opalescence_first):
    """The famous interaction, and Opalescence wins.

    Layer 4: Opalescence animates every *other* non-Aura enchantment, so
    Humility becomes a creature but Opalescence does not.

    Layer 6: Humility removes all abilities from all creatures. Humility is a
    creature, so it strips its own abilities. Opalescence is not a creature, so
    it keeps its own.

    Layer 7b: Humility's "base 1/1" came from an ability that no longer exists,
    so it never applies. Opalescence's does. Humility ends up a 4/4 - its own
    mana value - and Ghostly Prison a 3/3.

    The answer does not depend on which entered first, because layers decide
    the outcome and timestamps only order effects *within* a layer.
    """
    b = make_board(board.db, board.scripts)
    if opalescence_first:
        opal, hum = b.play("Opalescence"), b.play("Humility")
    else:
        hum, opal = b.play("Humility"), b.play("Opalescence")
    prison = b.play("Ghostly Prison")

    humility_chars = b.game.characteristics(hum)
    assert humility_chars.has_type(CardType.CREATURE), "layer 4 animation persists"
    assert humility_chars.abilities == (), "Humility strips its own abilities"
    assert b.pt(hum) == (4, 4), "Opalescence sets it to its mana value"

    assert not b.game.characteristics(opal).has_type(CardType.CREATURE)
    assert b.game.characteristics(opal).abilities, "Opalescence keeps its ability"
    assert b.pt(prison) == (3, 3)


# ---------------------------------------------------------------------------
# Layer 4: types, and CR 305.6 / 305.7 land abilities
# ---------------------------------------------------------------------------


def test_basic_land_types_carry_intrinsic_mana_abilities(board):
    """CR 305.6: a Forest taps for green without any printed text."""
    assert board.taps_for(board.play("Forest")) == {"G"}
    assert board.taps_for(board.play("Island")) == {"U"}


def test_dual_land_taps_for_both(board):
    assert board.taps_for(board.play("Bayou")) == {"B", "G"}


def test_urborg_makes_every_land_a_swamp(board):
    """And the Swamp type brings "{T}: Add {B}" with it."""
    forest = board.play("Forest")
    board.play("Urborg, Tomb of Yawgmoth")
    assert "Swamp" in board.game.characteristics(forest).subtypes
    assert board.taps_for(forest) == {"G", "B"}


def test_blood_moon_replaces_nonbasic_land_types(board):
    """CR 305.7: the old types go, and so do the abilities they granted."""
    bayou = board.play("Bayou")
    board.play("Blood Moon")
    chars = board.game.characteristics(bayou)
    assert chars.subtypes == ("Mountain",)
    assert board.taps_for(bayou) == {"R"}


def test_blood_moon_spares_basic_lands(board):
    forest = board.play("Forest")
    board.play("Blood Moon")
    assert board.taps_for(forest) == {"G"}


def test_blood_moon_shuts_off_urborg_regardless_of_order(board):
    """The dependency case (CR 613.8a).

    Urborg's effect depends on Blood Moon: Blood Moon strips Urborg's ability
    under CR 305.7, changing the *existence* of Urborg's effect. So Blood Moon
    applies first no matter which entered the battlefield first, and no land
    ends up a Swamp.
    """
    for order in ("urborg-first", "moon-first"):
        b = make_board(board.db, board.scripts)
        bayou = b.play("Bayou")
        if order == "urborg-first":
            b.play("Urborg, Tomb of Yawgmoth")
            b.play("Blood Moon")
        else:
            b.play("Blood Moon")
            b.play("Urborg, Tomb of Yawgmoth")

        chars = b.game.characteristics(bayou)
        assert "Swamp" not in chars.subtypes, f"{order}: Blood Moon must win"
        assert b.taps_for(bayou) == {"R"}, f"{order}"


def test_urborg_without_blood_moon_still_works(board):
    """The dependency must not fire when Blood Moon is absent."""
    bayou = board.play("Bayou")
    board.play("Urborg, Tomb of Yawgmoth")
    assert "Swamp" in board.game.characteristics(bayou).subtypes


# ---------------------------------------------------------------------------
# Layer 5: color
# ---------------------------------------------------------------------------


def test_color_change(board):
    bears = board.play("Grizzly Bears")
    assert board.game.characteristics(bears).colors == Color.GREEN

    board.scripts.add(
        "Painter's Servant",
        Ability.static(
            Effect(
                EffectKind.SET_COLOR,
                targets=ObjectFilter(zones=frozenset({bears.zone})),
                colors=Color.BLUE,
            ),
            text="All cards are blue.",
        ),
    )
    board.play("Painter's Servant")
    assert board.game.characteristics(bears).colors == Color.BLUE


# ---------------------------------------------------------------------------
# Determinism and caching
# ---------------------------------------------------------------------------


def test_repeated_computation_is_stable(board):
    bears = board.play("Grizzly Bears")
    board.play("Glorious Anthem")
    board.play("Humility")
    first = board.pt(bears)
    for _ in range(5):
        board.refresh()
        assert board.pt(bears) == first


def test_cache_invalidates_when_the_board_changes(board):
    bears = board.play("Grizzly Bears")
    assert board.pt(bears) == (2, 2)
    board.play("Glorious Anthem")
    assert board.pt(bears) == (3, 3)
