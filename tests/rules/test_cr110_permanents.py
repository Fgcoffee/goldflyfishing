"""Permanents (CR 110): what one is, who owns it, and what it is made of.

CR 110.1 defines a permanent by its *zone* and by nothing else. Every other
rule in CR 110 hangs off that, and so does the rule that is easiest to break by
accident: CR 110.4c, a permanent that has somehow lost all six permanent types
is still a permanent and stays on the battlefield. A single state-based action
written as "if this is not a creature, an artifact, an enchantment..." would
sweep it away, and nothing else in the engine would notice.

The other half of "defined by its zone" is the half the engine had wrong. A
zone change makes a new object (CR 400.7) and deliberately leaves the old one
behind still reading the zone it left, because last-known information needs it
(CR 603.6e, 704.8). Asking "is its zone the battlefield?" therefore says yes to
that husk, and ``destroy`` said yes with it: destroying an already-dead
permanent a second time moved it again and put a second copy of the card into
the graveyard.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import (
    PERMANENT_TYPES,
    CardType,
    Duration,
    Layer,
    Zone,
)
from mtgfish.rules.kernel.game import ContinuousEffect
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.rules.kernel.values import Value


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def lose_types(board, obj, types=PERMANENT_TYPES):
    """Layer 4: ``obj`` stops being the given card types (CR 613.1d).

    Defaults to all six permanent types at once, which is the state CR 110.4c
    is about. No card does exactly this, which is the point - the rule exists
    for the combinations of effects that get there.
    """
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.REMOVE_TYPE,
                targets=ObjectFilter(specific=(obj.id,)),
                types=types,
            ),
            source=NO_OBJECT,
            controller=PlayerId(0),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.TYPE),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.refresh()
    board.chars(obj)


# ---------------------------------------------------------------------------
# CR 110.1: an object on the battlefield, and only the live one
# ---------------------------------------------------------------------------


def test_a_permanent_is_an_object_on_the_battlefield(board):
    """CR 110.1. The control for everything below."""
    bears = board.play("Grizzly Bears", controller=0)

    assert bears.is_permanent


def test_a_card_in_hand_is_not_a_permanent(board):
    """The other control: the same card, one zone over."""
    assert not board.hand("Grizzly Bears", controller=0).is_permanent


def test_the_object_a_dead_permanent_left_behind_is_not_a_permanent(board):
    """CR 400.7 with CR 110.1: the husk keeps its old zone for last-known
    information, so the zone alone is the wrong question to ask."""
    bears = board.play("Grizzly Bears", controller=0)
    actions.destroy(board.game, bears)

    assert bears.zone is Zone.BATTLEFIELD
    assert not bears.is_live
    assert not bears.is_permanent


def test_destroying_the_same_permanent_twice_does_not_duplicate_the_card(board):
    """The bug CR 110.1 exists to stop here: a second destroy on the husk moved
    it a second time, and the graveyard ended up holding two Grizzly Bears."""
    bears = board.play("Grizzly Bears", controller=0)

    assert actions.destroy(board.game, bears) is True
    assert actions.destroy(board.game, bears) is False
    assert board.in_graveyard(0) == ["Grizzly Bears"]


def test_sacrificing_a_dead_permanent_does_nothing(board):
    bears = board.play("Grizzly Bears", controller=0)
    actions.destroy(board.game, bears)

    assert actions.sacrifice(board.game, bears) is False
    assert board.in_graveyard(0) == ["Grizzly Bears"]


def test_tapping_a_dead_permanent_does_nothing(board):
    """Tapping a husk did nothing visible and still reported that it had - so
    any cost or ability paid with the tap was paid for free."""
    bears = board.play("Grizzly Bears", controller=0)
    actions.destroy(board.game, bears)

    assert actions.tap(board.game, bears) is False


def test_a_live_permanent_still_taps(board):
    """The control: nothing above is about refusing ordinary work."""
    bears = board.play("Grizzly Bears", controller=0)

    assert actions.tap(board.game, bears) is True
    assert actions.untap(board.game, bears) is True


# ---------------------------------------------------------------------------
# CR 110.2: owner and controller
# ---------------------------------------------------------------------------


def test_a_permanents_owner_is_the_owner_of_its_card(board):
    """CR 110.2."""
    bears = board.play("Grizzly Bears", controller=1)

    assert bears.owner == PlayerId(1)
    assert bears.controller == PlayerId(1)


def test_a_permanent_put_onto_the_battlefield_enters_under_that_players_control(board):
    """CR 110.2a: the player the effect names controls it, whoever owns it."""
    card = board.hand("Grizzly Bears", controller=1)

    permanent = board.game.move_object(card, Zone.BATTLEFIELD, to_player=PlayerId(0))

    assert permanent.controller == PlayerId(0)
    assert permanent.owner == PlayerId(1), "CR 110.2: the card's owner is unchanged"


def test_control_reverts_to_the_player_who_put_it_onto_the_battlefield(board):
    """CR 110.2's "by default", which CR 613.1b re-derives every recomputation
    from ``base_controller`` - so a control change that ends gives it back."""
    card = board.hand("Grizzly Bears", controller=1)
    permanent = board.game.move_object(card, Zone.BATTLEFIELD, to_player=PlayerId(0))

    assert permanent.base_controller == PlayerId(0)


def test_a_tokens_owner_is_its_controller(board):
    """CR 110.2 names one exception and CR 111.2 states it: a token has no card
    behind it, so the player it entered under the control of owns it."""
    token = board.token("Grizzly Bears", controller=1)

    assert token.owner == PlayerId(1)


# ---------------------------------------------------------------------------
# CR 110.3: printed characteristics, as modified
# ---------------------------------------------------------------------------


def test_a_nontoken_permanent_starts_from_what_is_printed_on_its_card(board):
    """CR 110.3, first half."""
    bears = board.play("Grizzly Bears", controller=0)

    assert board.pt(bears) == (2, 2)
    assert board.chars(bears).name == "Grizzly Bears"


def test_a_continuous_effect_modifies_it_and_nothing_else_does(board):
    """CR 110.3, second half - which is CR 613 and is tested at length there.
    Here only to pin that the two halves are the same question."""
    bears = board.play("Grizzly Bears", controller=0)
    lose_types(board, bears, CardType.CREATURE)

    assert not board.chars(bears).is_creature
    assert board.game.printed_characteristics(bears).is_creature, (
        "the printed card is untouched; only the layered answer changed"
    )


# ---------------------------------------------------------------------------
# CR 110.4: the six permanent types
# ---------------------------------------------------------------------------


def test_there_are_six_permanent_types(board):
    """CR 110.4, listed rather than derived, so a seventh cannot creep in."""
    assert tuple(PERMANENT_TYPES) == (
        CardType.ARTIFACT,
        CardType.BATTLE,
        CardType.CREATURE,
        CardType.ENCHANTMENT,
        CardType.LAND,
        CardType.PLANESWALKER,
    )


def test_instants_and_sorceries_are_not_permanent_types(board):
    """CR 110.4: they can't enter the battlefield, so they can't be permanents.
    The enforcement is CR 304.4 / 307.4 and lives in its own test file."""
    assert not PERMANENT_TYPES & CardType.INSTANT
    assert not PERMANENT_TYPES & CardType.SORCERY


# ---------------------------------------------------------------------------
# CR 110.4c: losing every permanent type
# ---------------------------------------------------------------------------


def test_a_permanent_that_loses_all_its_permanent_types_stays_put(board):
    """CR 110.4c: it remains on the battlefield and is still a permanent."""
    bears = board.play("Grizzly Bears", controller=0)
    lose_types(board, bears)

    assert board.chars(bears).type_line.types is CardType.NONE
    assert bears.is_permanent
    assert bears.id in board.game.battlefield


def test_the_state_based_actions_do_not_sweep_up_a_typeless_permanent(board):
    """The way this rule is usually broken: one state-based action that asks
    what a permanent *is* rather than what it has."""
    bears = board.play("Grizzly Bears", controller=0)
    lose_types(board, bears)

    board.sba()

    assert board.alive(0) == ["Grizzly Bears"]


def test_it_is_no_longer_a_creature_with_a_toughness(board):
    """Why the state-based actions leave it alone, stated the other way round:
    CR 208.3 gives power and toughness only to a creature, so CR 704.5f has no
    toughness to find. It is not that the rule was suppressed for this object."""
    bears = board.play("Grizzly Bears", controller=0)
    lose_types(board, bears)

    assert board.pt(bears) == (None, None)


def test_a_creature_with_no_toughness_left_still_dies(board):
    """The control for the two tests above: CR 704.5f is working, and a
    typeless permanent survives because the rule does not reach it."""
    bears = board.play("Grizzly Bears", controller=0)
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.MODIFY_PT,
                targets=ObjectFilter(specific=(bears.id,)),
                amount=Value(constant=-2),
                amount2=Value(constant=-2),
            ),
            source=NO_OBJECT,
            controller=PlayerId(0),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.PT_MODIFY),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.refresh()

    board.sba()

    assert board.alive(0) == []


def test_a_typeless_former_planeswalker_keeps_its_place(board):
    """CR 110.4c again, against the state-based action most likely to catch it:
    CR 704.5i, which puts a planeswalker with no loyalty into the graveyard."""
    jace = board.play("Jace Beleren", controller=0)
    assert jace.counter_count("loyalty") == 0
    lose_types(board, jace)

    board.sba()

    assert jace.is_permanent


def test_a_planeswalker_with_no_loyalty_still_dies(board):
    """The control: CR 704.5i is working, and the test above is not passing
    because loyalty happened to be irrelevant."""
    jace = board.play("Jace Beleren", controller=0)
    assert jace.counter_count("loyalty") == 0

    board.sba()

    assert not jace.is_permanent


def test_a_typeless_permanent_can_still_be_destroyed(board):
    """It is a permanent, so every rule about permanents still reaches it."""
    bears = board.play("Grizzly Bears", controller=0)
    lose_types(board, bears)

    assert actions.destroy(board.game, bears) is True
    assert board.in_graveyard(0) == ["Grizzly Bears"]


# ---------------------------------------------------------------------------
# CR 110.2b: an effect that takes hold of a permanent spell
# ---------------------------------------------------------------------------


def _permanent_spell(board, name: str, controller: int):
    """A permanent spell on the stack, cast by ``controller``."""
    card = board.hand(name, controller=controller)
    spell = board.game.move_object(card, Zone.STACK, to_player=PlayerId(controller))
    spell.controller = PlayerId(controller)
    spell.base_controller = PlayerId(controller)
    board.game.stack.append(spell.id)
    return spell


def _gain_control(board, obj, thief: int, zones):
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.GAIN_CONTROL,
                targets=ObjectFilter(specific=(obj.id,), zones=frozenset(zones)),
            ),
            source=NO_OBJECT,
            controller=PlayerId(thief),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.CONTROL),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.refresh()
    board.chars(obj)


def test_stealing_a_permanent_spell_gives_the_thief_the_permanent(board):
    """CR 110.2b, first half: the player who gained control of the spell
    controls the permanent it becomes."""
    spell = _permanent_spell(board, "Grizzly Bears", controller=0)
    _gain_control(board, spell, thief=1, zones={Zone.STACK})
    assert spell.controller == PlayerId(1)

    resolve_top(board.game)
    permanent = board.game.objects[spell.superseded_by]

    assert permanent.controller == PlayerId(1)
    assert permanent.owner == PlayerId(0), "CR 110.2: the card's owner is unchanged"


def test_a_stolen_permanent_spell_still_defaults_to_the_player_who_cast_it(board):
    """CR 110.2b, second half: the permanent's controller *by default* is the
    player who put the spell on the stack, so when the theft ends control goes
    back to them and not to the thief. ``base_controller`` is where CR 613.1b
    reads that default from, and the resolution overwrites it with the thief.
    """
    spell = _permanent_spell(board, "Grizzly Bears", controller=0)
    _gain_control(board, spell, thief=1, zones={Zone.STACK})

    resolve_top(board.game)
    permanent = board.game.objects[spell.superseded_by]

    assert permanent.base_controller == PlayerId(0)


def test_an_unstolen_permanent_spell_defaults_to_its_caster(board):
    """The control: with no theft the two answers coincide, which is why the
    bug above is invisible in every ordinary game."""
    spell = _permanent_spell(board, "Grizzly Bears", controller=0)

    resolve_top(board.game)
    permanent = board.game.objects[spell.superseded_by]

    assert permanent.base_controller == PlayerId(0)
    assert permanent.controller == PlayerId(0)
