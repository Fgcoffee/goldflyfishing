"""Spells (CR 112): a card on the stack, and what stays true of it afterwards.

CR 112.1 is a definition the engine gets for free - an object's zone says
whether it is a spell, and ``ObjectKind`` says whether it is an ability rather
than one. CR 112.1a extends that to copies, which have no card of their own,
and CR 112.2 gives the copy a different owner from the card it copies.

CR 112.4 is the rule with teeth, and it is the one the engine does not keep. An
effect that changed a permanent spell on the stack goes on applying to the
permanent that spell becomes - which cuts directly across CR 400.7, the rule
that a zone change makes a new object with no memory of the old one. The engine
implements CR 400.7 and has no exception for this, so the two tests marked
``xfail`` below record exactly what is missing rather than leaving it to be
rediscovered.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import (
    Resolution,
    _register_continuous,
)
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import Color, Duration, Layer, Zone
from mtgfish.rules.kernel.game import ContinuousEffect
from mtgfish.rules.kernel.gameobject import GameObject, ObjectKind
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def spell(board, name: str, controller: int = 0) -> GameObject:
    """A card put on the stack, as step one of casting it does (CR 601.2a)."""
    card = board.hand(name, controller=controller)
    obj = board.game.move_object(card, Zone.STACK, to_player=PlayerId(controller))
    obj.controller = PlayerId(controller)
    obj.base_controller = PlayerId(controller)
    board.game.stack.append(obj.id)
    board.refresh()
    return obj


def ability_on_the_stack(board, source: GameObject) -> GameObject:
    """CR 113.7: an activated or triggered ability on the stack is an object,
    and is the one object there that is not a spell."""
    obj = GameObject(
        id=board.game.ids.object_id(),
        kind=ObjectKind.ABILITY,
        owner=source.owner,
        controller=source.controller,
        zone=Zone.STACK,
        ability=Ability(AbilityKind.ACTIVATED, text="{T}: draw a card"),
        source=source.id,
        timestamp=board.game.ids.timestamp(),
    )
    board.game.objects[obj.id] = obj
    board.game.stack.append(obj.id)
    return obj


def paint_white(board, obj, zones):
    """A continuous effect that makes ``obj`` white, scoped to ``zones``."""
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.SET_COLOR,
                targets=ObjectFilter(specific=(obj.id,), zones=frozenset(zones)),
                colors=Color.WHITE,
            ),
            source=NO_OBJECT,
            controller=PlayerId(0),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.COLOR),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.refresh()


# ---------------------------------------------------------------------------
# CR 112.1: a card on the stack
# ---------------------------------------------------------------------------


def test_a_card_on_the_stack_is_a_spell(board):
    """CR 112.1."""
    assert spell(board, "Grizzly Bears").is_spell


def test_a_card_in_hand_is_not_a_spell(board):
    """CR 112.1: it becomes one as the first step of being cast, not before."""
    assert not board.hand("Grizzly Bears", controller=0).is_spell


def test_a_permanent_is_not_a_spell(board):
    """The same card once it has resolved - CR 112.1's "until it resolves"."""
    assert not board.play("Grizzly Bears", controller=0).is_spell


def test_a_spell_stops_being_one_when_it_resolves(board):
    """CR 112.1 with CR 608.3: the permanent spell becomes a permanent, and the
    object that was a spell is not the object that is now on the battlefield."""
    cast = spell(board, "Grizzly Bears")

    resolve_top(board.game)
    permanent = board.game.objects[cast.superseded_by]

    assert permanent.is_permanent
    assert not permanent.is_spell
    assert board.game.stack == []


def test_an_ability_on_the_stack_is_not_a_spell(board):
    """CR 112.1 read as an exclusion, which CR 113.7 states from the other
    side: the stack holds spells and abilities, and only one of them is a
    spell. Countering "target spell" must not reach an ability."""
    bears = board.play("Grizzly Bears", controller=0)
    ability = ability_on_the_stack(board, bears)

    assert ability.zone is Zone.STACK
    assert not ability.is_spell
    assert ability.is_ability_on_stack


# ---------------------------------------------------------------------------
# CR 112.1a: a copy of a spell
# ---------------------------------------------------------------------------


def test_a_copy_of_a_spell_is_also_a_spell(board):
    """CR 112.1a: "even if it has no card associated with it"."""
    original = spell(board, "Grizzly Bears")

    copy = copy_spell(board.game, original)

    assert copy is not None
    assert copy.kind is ObjectKind.COPY
    assert copy.is_spell


def test_a_copy_of_an_ability_is_not_a_spell(board):
    """The control: the same call on an ability makes another ability."""
    bears = board.play("Grizzly Bears", controller=0)
    original = ability_on_the_stack(board, bears)

    copy = copy_spell(board.game, original)

    assert copy is not None
    assert not copy.is_spell


def test_only_a_spell_on_the_stack_can_be_copied(board):
    """CR 707.10 copies a spell, which by CR 112.1 means one on the stack."""
    assert copy_spell(board.game, board.play("Grizzly Bears", controller=0)) is None


# ---------------------------------------------------------------------------
# CR 112.2: owner and controller
# ---------------------------------------------------------------------------


def test_a_spells_owner_is_the_owner_of_its_card(board):
    """CR 112.2, first sentence."""
    cast = spell(board, "Grizzly Bears", controller=1)

    assert cast.owner == PlayerId(1)
    assert cast.controller == PlayerId(1)


def test_a_copys_owner_is_the_player_who_put_it_on_the_stack(board):
    """CR 112.2's exception: a copy has no card, so the owner is whoever put it
    there - which is not the owner of the spell it copies."""
    original = spell(board, "Grizzly Bears", controller=1)

    copy = copy_spell(board.game, original, controller=PlayerId(0))

    assert copy.owner == PlayerId(0)
    assert copy.controller == PlayerId(0)
    assert original.owner == PlayerId(1), "the original is untouched"


# ---------------------------------------------------------------------------
# CR 112.3: printed characteristics, as modified
# ---------------------------------------------------------------------------


def test_a_noncopy_spell_starts_from_what_is_printed_on_its_card(board):
    """CR 112.3, first half."""
    cast = spell(board, "Grizzly Bears")

    assert board.chars(cast).name == "Grizzly Bears"
    assert board.chars(cast).colors is Color.GREEN


def test_a_continuous_effect_changes_a_spell_on_the_stack(board):
    """CR 112.3, second half, and the control for CR 112.4 below: the layer
    system really does compute stack objects, so an effect that names a spell
    changes it while it is still a spell."""
    cast = spell(board, "Grizzly Bears")

    paint_white(board, cast, zones={Zone.STACK})

    assert board.chars(cast).colors is Color.WHITE


def test_an_ability_granted_to_a_spell_applies_on_the_stack(board):
    """CR 610.5, which is why stack objects are in the layer system's scope at
    all - and the same machinery CR 112.3 and CR 112.4 need."""
    cast = spell(board, "Grizzly Bears")
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.GRANT_ABILITY,
                targets=ObjectFilter(
                    specific=(cast.id,), zones=frozenset({Zone.STACK})
                ),
                granted_abilities=(keyword("Flying"),),
            ),
            source=NO_OBJECT,
            controller=PlayerId(0),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.ABILITY),
            duration=int(Duration.PERMANENT),
            created_turn=board.game.turn,
        )
    )
    board.refresh()

    assert "Flying" in board.keywords(cast)


# ---------------------------------------------------------------------------
# CR 112.4: the effect follows the spell onto the battlefield
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="CR 112.4 is not implemented: CR 400.7 makes the permanent a new "
    "object and nothing re-points the effect at it",
)
def test_an_effect_on_a_permanent_spell_keeps_applying_to_the_permanent(board):
    """CR 112.4, which is the rule's own example: a creature spell made white
    enters the battlefield white and stays white for the effect's duration.

    The effect's set of objects was settled on the spell (CR 611.2c), the spell
    became a new object on resolution (CR 400.7), and the effect names the old
    one - so the permanent enters with its printed colour back.
    """
    cast = spell(board, "Grizzly Bears")
    paint_white(board, cast, zones={Zone.STACK, Zone.BATTLEFIELD})
    assert board.chars(cast).colors is Color.WHITE

    resolve_top(board.game)
    permanent = board.game.objects[cast.superseded_by]

    assert board.chars(permanent).colors is Color.WHITE


@pytest.mark.xfail(
    strict=True,
    reason="resolve._register_continuous scopes every settled effect to the "
    "battlefield, so no resolving spell can reach a spell on the stack",
)
def test_a_resolving_spell_can_create_an_effect_that_reaches_the_stack(board):
    """The root cause under CR 112.4, one step earlier than the test above.

    CR 611.2c settles which objects a characteristic-changing effect applies
    to when it begins. ``_register_continuous`` records that set as a filter
    and hard-codes the battlefield as its zone, so an effect that targeted a
    *spell* is born unable to see it - CR 112.4 never gets the chance to fail.
    """
    target = spell(board, "Grizzly Bears")
    effect = Effect(
        EffectKind.SET_COLOR,
        targets=ObjectFilter(specific=(target.id,), zones=frozenset({Zone.STACK})),
        colors=Color.WHITE,
        is_targeted=True,
    )
    resolution = Resolution(
        game=board.game,
        source=NO_OBJECT,
        controller=PlayerId(0),
        targets=((target.id,),),
    )

    _register_continuous(resolution, effect)

    registered = board.game.continuous_effects[-1]
    assert Zone.STACK in registered.effect.targets.zones


def test_an_effect_on_a_permanent_keeps_applying_to_that_permanent(board):
    """The control: nothing above is a claim that continuous effects are
    broken. An effect settled on a permanent goes on applying to it, which is
    the ordinary case and the one CR 112.4 extends."""
    bears = board.play("Grizzly Bears", controller=0)
    paint_white(board, bears, zones={Zone.BATTLEFIELD})

    assert board.chars(bears).colors is Color.WHITE
