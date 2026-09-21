"""Named interactions that only come out right if the rules are modelled properly.

Each of these has a published, checkable answer, and each fails under a
plausible-looking shortcut:

* **Two Opalescences and a Humility** - fails unless layer 6 ability removal
  genuinely stops those abilities producing layer 7 effects (CR 613.6).
* **Rain of Gore and lifelink** - fails unless lifelink's life gain is a
  replaceable event rather than a direct addition (CR 614, 702.15).
* **Urza's Saga and Blood Moon** - fails unless CR 305.7 removes land types
  only, and unless the Saga sacrifice check is conditioned on *having chapter
  abilities* (CR 714.4).
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.actions import deal_damage, gain_life
from mtgfish.rules.cr118_costs import TAP_COST
from mtgfish.rules.cr614_replacement import ReplacementEffect, ReplacementKind, register
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import CardType, Color, Supertype
from mtgfish.rules.events import EventKind
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.query import ObjectFilter, PlayerFilter, PlayerScope, Value, ValueKind

ALL_CREATURES = ObjectFilter(types_all=CardType.CREATURE)
NONBASIC_LANDS = ObjectFilter(types_all=CardType.LAND, supertypes_none=Supertype.BASIC)


# ---------------------------------------------------------------------------
# Two Opalescences and a Humility
# ---------------------------------------------------------------------------


def opalescence() -> Ability:
    """"Each other non-Aura enchantment is a creature ... equal to its mana value"."""
    others = ObjectFilter(
        types_all=CardType.ENCHANTMENT, subtypes_none=("Aura",), other_than_source=True
    )
    its_mana_value = Value(ValueKind.MANA_VALUE, of_affected=True)
    return Ability.static(
        Effect(EffectKind.ADD_TYPE, targets=others, types=CardType.CREATURE),
        Effect(
            EffectKind.SET_PT, targets=others, amount=its_mana_value, amount2=its_mana_value
        ),
        text="Opalescence",
    )


def humility() -> Ability:
    """"All creatures lose all abilities and have base power and toughness 1/1"."""
    return Ability.static(
        Effect(EffectKind.REMOVE_ABILITIES, targets=ALL_CREATURES),
        Effect(
            EffectKind.SET_PT, targets=ALL_CREATURES, amount=Value.of(1), amount2=Value.of(1)
        ),
        text="Humility",
    )


@pytest.fixture
def enchantment_board(card_db):
    scripts = ScriptedAbilities(
        {
            "Opalescence": (opalescence(),),
            # A second Opalescence has to be a different card name to sit on the
            # battlefield alongside the first without the legend rule; neither
            # is legendary, so any second copy works - and Opalescence is not
            # itself unique, so two really can coexist.
            "Humility": (humility(),),
            "Ghostly Prison": (),
        }
    )
    return make_board(card_db, scripts)


def test_one_opalescence_and_humility_leaves_humility_alive(enchantment_board):
    """The baseline: Opalescence is not a creature, so it keeps its ability.

    Humility becomes a creature in layer 4, strips its own abilities in layer
    6, and Opalescence sets it to its own mana value in 7b. Humility is {2}{W}{W},
    so it is a 4/4.
    """
    board = enchantment_board
    board.play("Opalescence")
    humility_permanent = board.play("Humility")

    assert board.pt(humility_permanent) == (4, 4)
    assert board.game.characteristics(humility_permanent).abilities == ()


def test_two_opalescences_and_humility_kills_everything(enchantment_board):
    """The published answer: they all become 0/0 and die.

    Each Opalescence animates the *other* one, so this time both Opalescences
    are creatures. In layer 6 Humility strips abilities from all creatures -
    including both Opalescences and itself. In layer 7b there is therefore no
    power-and-toughness-setting effect left in existence at all, so nothing
    defines their power or toughness and they settle at 0/0 (CR 613.6, 704.5f).

    An engine that gathers continuous effects once, before layer 1, gets 4/4s
    here and never notices.
    """
    board = enchantment_board
    # Ghostly Prison stands in for a second Opalescence: it must be a non-Aura
    # *enchantment*, so that each copy animates the other. A land carrying the
    # same ability would never become a creature, Humility would never strip
    # it, and the test would quietly prove nothing.
    board.scripts.add("Ghostly Prison", opalescence())

    first = board.play("Opalescence")
    second = board.play("Ghostly Prison")
    hum = board.play("Humility")

    for permanent in (first, second, hum):
        chars = board.game.characteristics(permanent)
        assert chars.has_type(CardType.CREATURE), f"{chars.name} should be animated"
        assert chars.abilities == (), f"{chars.name} should have lost its abilities"
        assert board.pt(permanent) == (0, 0), f"{chars.name} should be 0/0"

    board.sba()
    for permanent in (first, second, hum):
        assert permanent.id not in board.game.battlefield


# ---------------------------------------------------------------------------
# Rain of Gore and lifelink
# ---------------------------------------------------------------------------


def rain_of_gore(game, controller: PlayerId) -> ReplacementEffect:
    """"If a spell or ability would cause its controller to gain life, that
    player loses that much life instead"."""
    return register(
        game,
        ReplacementEffect(
            kind=ReplacementKind.LIFE_GAIN_BECOMES_LOSS,
            event_kinds=frozenset({EventKind.LIFE_GAINED}),
            players=PlayerFilter(PlayerScope.EACH_PLAYER),
            controller=controller,
            text="Rain of Gore",
        ),
    )


@pytest.fixture
def gore_board(card_db):
    scripts = ScriptedAbilities(
        {"Vampire Nighthawk": (keyword("Flying"), keyword("Deathtouch"), keyword("Lifelink"))}
    )
    return make_board(card_db, scripts)


def test_plain_life_gain_becomes_life_loss(gore_board):
    board = gore_board
    rain_of_gore(board.game, PlayerId(0))

    gain_life(board.game, PlayerId(0), 5)
    assert board.game.player(PlayerId(0)).life == 35


def test_lifelink_becomes_life_loss(gore_board):
    """The interaction proper.

    Lifelink is not a triggered ability - it is part of the damage event
    (CR 702.15a) - so the life gain it causes must go through the same
    replaceable path as any other life gain. A 2/3 lifelinker dealing 2 damage
    under Rain of Gore drains its own controller for 2.
    """
    board = gore_board
    game = board.game
    hawk = board.play("Vampire Nighthawk", controller=0)  # 2/3 lifelink
    rain_of_gore(game, PlayerId(0))

    deal_damage(
        game,
        PlayerId(1),
        2,
        source=hawk.id,
        source_controller=PlayerId(0),
        lifelink=True,
        combat=True,
    )

    assert game.player(PlayerId(1)).life == 38, "the damage is still dealt"
    assert game.player(PlayerId(0)).life == 38, "and the lifelink gain became a loss"


def test_the_damage_still_happens(gore_board):
    """Rain of Gore replaces the life gain, not the damage."""
    board = gore_board
    game = board.game
    hawk = board.play("Vampire Nighthawk", controller=0)
    rain_of_gore(game, PlayerId(0))
    victim = board.play("Grizzly Bears", controller=1)

    deal_damage(
        game, victim, 2, source=hawk.id, source_controller=PlayerId(0), lifelink=True
    )
    assert victim.damage == 2


def test_no_life_gain_event_is_emitted(gore_board):
    """The gain never happened, so nothing that watches for it sees anything."""
    board = gore_board
    game = board.game
    game.log.enabled = True
    rain_of_gore(game, PlayerId(0))

    before = len(game.log)
    gain_life(game, PlayerId(0), 3)
    emitted = " ".join(e.text for e in game.log.entries[before:])

    assert "LIFE_GAINED" not in emitted
    assert "LIFE_LOST" in emitted


# ---------------------------------------------------------------------------
# Urza's Saga and Blood Moon
# ---------------------------------------------------------------------------


def blood_moon() -> Ability:
    """"Nonbasic lands are Mountains"."""
    return Ability.static(
        Effect(
            EffectKind.SET_TYPE,
            targets=NONBASIC_LANDS,
            types=CardType.LAND,
            keywords=("Mountain",),
        ),
        text="Nonbasic lands are Mountains.",
    )


def saga_chapters() -> tuple[Ability, ...]:
    """Urza's Saga's three chapter abilities, as chapter-numbered abilities.

    Only the chapter numbering matters here: CR 714.4's sacrifice check asks
    whether the Saga has any chapter abilities at all.
    """
    def chapter(number: int) -> Ability:
        return Ability(
            AbilityKind.TRIGGERED,
            effects=(Effect(EffectKind.NOTHING, text=f"chapter {number}"),),
            chapter=number,
            text=f"Chapter {number}",
        )

    granted = Ability(
        AbilityKind.ACTIVATED,
        effects=(Effect(EffectKind.ADD_MANA, colors=Color.NONE, text="Add {C}"),),
        cost=TAP_COST,
        is_mana_ability=True,
        text="{T}: Add {C}.",
    )
    return (chapter(1), chapter(2), chapter(3), granted)


@pytest.fixture
def saga_board(card_db):
    scripts = ScriptedAbilities(
        {"Blood Moon": (blood_moon(),), "Urza's Saga": saga_chapters()}
    )
    return make_board(card_db, scripts)


def test_urzas_saga_is_an_enchantment_land(saga_board):
    saga = saga_board.play("Urza's Saga")
    chars = saga_board.game.characteristics(saga)
    assert chars.has_type(CardType.LAND)
    assert chars.has_type(CardType.ENCHANTMENT)
    assert chars.has_subtype("Saga")


def test_a_finished_saga_is_sacrificed(saga_board):
    """CR 714.4, the normal case: final chapter reached, so it goes."""
    board = saga_board
    saga = board.play("Urza's Saga")
    saga.add_counters("lore", 3)
    board.refresh()

    board.sba()
    assert saga.id not in board.game.battlefield


def test_blood_moon_makes_urzas_saga_a_mountain_with_no_abilities(saga_board):
    """CR 305.7: it loses its land types and every ability from its rules text."""
    board = saga_board
    saga = board.play("Urza's Saga")
    board.play("Blood Moon")

    chars = board.game.characteristics(saga)
    assert "Mountain" in chars.subtypes
    assert board.taps_for(saga) == {"R"}
    assert all(a.chapter == 0 for a in chars.abilities), "chapter abilities are gone"


def test_blood_moon_leaves_the_saga_subtype_alone(saga_board):
    """CR 305.7 removes *land* types. Saga is an enchantment type and survives."""
    board = saga_board
    saga = board.play("Urza's Saga")
    board.play("Blood Moon")

    chars = board.game.characteristics(saga)
    assert chars.has_subtype("Saga")
    assert chars.has_type(CardType.ENCHANTMENT)


def test_blood_moon_stops_urzas_saga_sacrificing_itself(saga_board):
    """The published answer: the Saga survives as a Mountain.

    CR 714.4 sacrifices a Saga "with one or more chapter abilities" whose lore
    counters have reached its final chapter. Blood Moon has removed every
    chapter ability, so there is no final chapter number and the state-based
    action does not apply - however many lore counters are sitting on it.
    """
    board = saga_board
    saga = board.play("Urza's Saga")
    saga.add_counters("lore", 3)
    board.play("Blood Moon")

    board.sba()
    assert saga.id in board.game.battlefield, "it survives as a Mountain"
    assert board.taps_for(saga) == {"R"}


def test_without_blood_moon_the_same_saga_dies(saga_board):
    """The control case, so the test above is not passing for the wrong reason."""
    board = saga_board
    saga = board.play("Urza's Saga")
    saga.add_counters("lore", 3)

    board.sba()
    assert saga.id not in board.game.battlefield
