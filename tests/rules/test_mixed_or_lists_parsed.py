""""Creature or Vehicle", "Equip legendary creature {3}", read from the
printed text and played on a board.

The parser emits a mixed type-or-subtype list as ``ObjectFilter.any_of``
and an equip quality as the instance's filter; these check that what it
emits reaches the right objects and no others.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def _candidates(board, source, effect):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _candidates_for

    return set(_candidates_for(board.game, source, effect, P0))


def _ability(game, obj, text):
    return next(a for a in game.characteristics(obj).abilities if text in a.text)


def test_bounce_off_reaches_creatures_and_vehicles_only(card_db):
    board = make_board(card_db, OracleAbilities())
    bears = board.play("Grizzly Bears", controller=1)
    vehicle = board.play("Smuggler's Copter", controller=1)
    other = board.play("Sol Ring", controller=1)
    (ability,) = parse_card(card_db.lookup("Bounce Off")).faces[0].abilities
    found = _candidates(board, bears, ability.effects[0])
    assert bears.id in found and vehicle.id in found
    assert other.id not in found


def test_skullport_merchant_sacrifices_a_creature_or_a_treasure(card_db):
    from mtgfish.rules.kernel.matching import matches

    board = make_board(card_db, OracleAbilities())
    merchant = board.play("Skullport Merchant", controller=0)
    bears = board.play("Grizzly Bears", controller=0)
    treasure = board.play("Buried Treasure", controller=0)
    ring = board.play("Sol Ring", controller=0)
    ability = _ability(board.game, merchant, "Sacrifice")
    (sacrifice,) = [c for c in ability.cost.components if c.filter is not None]
    fits = {
        o.id
        for o in (merchant, bears, treasure, ring)
        if matches(board.game, o, sacrifice.filter, source=merchant.id, controller=P0)
    }
    assert fits == {bears.id, treasure.id}


def test_equip_hero_reaches_only_your_heroes(card_db):
    board = make_board(card_db, OracleAbilities())
    armor = board.play("Hulkbuster Armor", controller=0)
    hero = board.play("The Whizzer, Classic Speedster", controller=0)
    bears = board.play("Grizzly Bears", controller=0)
    ability = _ability(board.game, armor, "Equip Hero")
    assert str(ability.cost) == "{3}"
    found = _candidates(board, armor, ability.effects[0])
    assert found == {hero.id}


def test_equip_planeswalker_is_not_read(card_db):
    """Luxior attaches to a planeswalker, which a condition on "creature you
    control" cannot say."""
    parsed = parse_card(card_db.lookup("Luxior, Giada's Gift"))
    assert any("Equip planeswalker" in f.text for f in parsed.faces[0].failures)
