"""An ability granted in layer 6 generates its own effect (CR 613.6, 611.3b).

The effect list was harvested once, before any layer ran, from the printed
characteristics. So an ability *added* by a continuous effect appeared on the
object and then did nothing: the reverse direction worked - an ability stripped
in layer 6 stops producing before its layer 7 effect applies - but nothing ever
looked again for abilities that had arrived.

Anything of the shape "creatures you control have 'this creature gets +1/+1 as
long as ...'" was inert, and the parser produces that shape.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, _register_continuous
from mtgfish.rules.kernel.enums import Duration
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.rules.kernel.values import Value


def _grant(board, obj, ability, controller=0):
    _register_continuous(
        Resolution(game=board.game, source=NO_OBJECT, controller=PlayerId(controller)),
        Effect(
            EffectKind.GRANT_ABILITY,
            targets=ObjectFilter(specific=(obj.id,)),
            granted_abilities=(ability,),
            duration=int(Duration.PERMANENT),
        ),
    )
    board.refresh()


def _static_pump(power, toughness, text="gets a bonus"):
    return Ability(
        AbilityKind.STATIC,
        effects=(
            Effect(
                EffectKind.MODIFY_PT,
                amount=Value(constant=power),
                amount2=Value(constant=toughness),
            ),
        ),
        text=text,
    )


def test_a_granted_static_ability_is_on_the_object(card_db):
    """The half that already worked, kept honest."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _grant(board, bear, _static_pump(3, 3))
    assert "gets a bonus" in [a.text for a in board.chars(bear).abilities]


def test_a_granted_static_ability_actually_applies(card_db):
    """The half that did not: it must generate its continuous effect."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _grant(board, bear, _static_pump(3, 3))
    assert board.pt(bear) == (5, 5)


def test_a_granted_keyword_still_works(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    _grant(board, bear, Ability(AbilityKind.STATIC, keyword="Flying", text="Flying"))
    assert "Flying" in board.keywords(bear)


def test_an_ungranted_creature_is_untouched(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    other = board.play("Savannah Lions")
    _grant(board, bear, _static_pump(3, 3))
    assert board.pt(bear) == (5, 5)
    assert board.pt(other) == (2, 1)


def test_a_printed_static_ability_is_not_applied_twice(card_db):
    """The re-harvest must not double-count what the first pass already had."""
    from harness import ScriptedAbilities

    scripts = ScriptedAbilities()
    board = make_board(card_db, scripts)
    bear = board.play("Grizzly Bears")
    scripts.add("Grizzly Bears", _static_pump(1, 1, "printed bonus"))
    board.refresh()
    assert board.pt(bear) == (3, 3)
