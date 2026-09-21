"""An attachment falls off a host it may no longer be on (CR 303.4c, 301.5).

The restriction was parsed and already on the object - the Enchant keyword
carries its filter as ``quality`` - and the state-based action ignored it,
returning True for any host that merely existed. So nothing ever fell off: an
Aura stayed on a creature that had stopped being a creature, and Equipment
stayed on a permanent that was no longer one, both still applying their effect.

This only became reachable when Auras started entering attached at all
(CR 303.4); before that they went straight to the graveyard.
"""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.query import ObjectFilter


def _on_battlefield(board, obj):
    """Whether it is still there.

    Not ``obj.zone``: a permanent put into the graveyard becomes a new object
    (CR 400.7) and the old one deliberately keeps its zone as last-known
    information, so reading it says BATTLEFIELD for ever.
    """
    return obj.id in board.game.battlefield


def _aura(scripts, name, quality):
    scripts.add(
        name,
        Ability(
            AbilityKind.STATIC,
            keyword="Enchant",
            quality=quality,
            text="Enchant",
        ),
    )


def test_an_aura_stays_on_a_legal_host(card_db):
    scripts = ScriptedAbilities()
    board = make_board(card_db, scripts)
    bear = board.play("Grizzly Bears")
    aura = board.play("Pacifism")
    _aura(scripts, "Pacifism", ObjectFilter(types_all=CardType.CREATURE))
    actions.attach(board.game, aura, bear)
    board.refresh()

    board.sba()
    assert _on_battlefield(board, aura)


def test_an_aura_falls_off_a_host_it_may_not_enchant(card_db):
    """"Enchant creature" on something that is not a creature."""
    scripts = ScriptedAbilities()
    board = make_board(card_db, scripts)
    land = board.play("Forest")
    aura = board.play("Pacifism")
    _aura(scripts, "Pacifism", ObjectFilter(types_all=CardType.CREATURE))
    actions.attach(board.game, aura, land)
    board.refresh()

    board.sba()
    assert not _on_battlefield(board, aura)


def test_an_enchant_land_aura_is_happy_on_a_land(card_db):
    scripts = ScriptedAbilities()
    board = make_board(card_db, scripts)
    land = board.play("Forest")
    aura = board.play("Pacifism")
    _aura(scripts, "Pacifism", ObjectFilter(types_all=CardType.LAND))
    actions.attach(board.game, aura, land)
    board.refresh()

    board.sba()
    assert _on_battlefield(board, aura)


def test_an_aura_with_no_readable_restriction_stays_put(card_db):
    """Destroying it on a restriction the engine cannot see would be worse."""
    board = make_board(card_db)
    land = board.play("Forest")
    aura = board.play("Pacifism")
    actions.attach(board.game, aura, land)
    board.refresh()

    board.sba()
    assert _on_battlefield(board, aura)


def test_equipment_falls_off_a_noncreature(card_db):
    """CR 301.5: Equipment goes on a creature, whatever its equip ability says."""
    board = make_board(card_db)
    land = board.play("Forest")
    gear = board.play("Bonesplitter")
    actions.attach(board.game, gear, land)
    board.refresh()

    board.sba()
    assert board.game.objects[gear.id].attached_to == 0


def test_equipment_stays_on_a_creature(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    gear = board.play("Bonesplitter")
    actions.attach(board.game, gear, bear)
    board.refresh()

    board.sba()
    assert board.game.objects[gear.id].attached_to == bear.id
