"""Curses: "Enchant player", "enchanted player", and the grammar they reach.

The rules engine lets an Aura enchant a player (CR 303.4a-c). Reading the
curses brought three older misreadings into view, each fixed or refused:

* "sacrifices a permanent of their choice unless they pay {1}" hung the
  "unless" on an empty "of their choice" and sacrificed unconditionally
  (Aether Barrier);
* "that player exiles two cards from their graveyard" dropped the player -
  you exiled any two cards from any graveyard (Curse of Oblivion,
  Witness the End);
* "that player or a planeswalker that player controls" let any player be
  hit (Curse of the Pierced Heart).
"""

from __future__ import annotations

from mtgfish.parser import parse_card
from mtgfish.rules.kernel.query import PlayerScope


def _abilities(card_db, name):
    return parse_card(card_db.lookup(name)).faces[0].abilities


def test_enchant_player_and_opponent(card_db):
    from mtgfish.rules.kernel.query import PlayerFilter

    (enchant,) = [a for a in _abilities(card_db, "Curse of the Bloody Tome") if a.keyword]
    assert enchant.quality == PlayerFilter(PlayerScope.EACH_PLAYER)
    (enchant,) = [a for a in _abilities(card_db, "Archnemesis") if a.keyword == "Enchant"]
    assert enchant.quality == PlayerFilter(PlayerScope.EACH_OPPONENT)


def test_enchanted_players_upkeep(card_db):
    (upkeep,) = [a for a in _abilities(card_db, "Curse of the Bloody Tome") if a.trigger]
    assert upkeep.trigger.players.scope is PlayerScope.ENCHANTED_PLAYER
    assert parse_card(card_db.lookup("Curse of the Bloody Tome")).fully_parsed


def test_sacrifice_of_their_choice_unless_they_pay(card_db):
    (ability,) = _abilities(card_db, "Aether Barrier")
    (effect,) = ability.effects
    assert effect.kind.name == "UNLESS_PAYS"
    assert [c.kind.name for c in effect.children] == ["SACRIFICE"]


def test_misread_shapes_are_unread(card_db):
    for name in (
        "Curse of Oblivion",
        "Witness the End",
        "Torment of Scarabs",
        "Curse of the Pierced Heart",
    ):
        assert not parse_card(card_db.lookup(name)).fully_parsed, name


def test_defending_player_exiles_from_their_own_library(card_db):
    (ability,) = [
        a for a in _abilities(card_db, "Ulamog, the Ceaseless Hunger") if "attacks" in a.text
    ]
    (exile,) = ability.effects
    assert exile.players is not None and exile.players.scope is PlayerScope.DEFENDING_PLAYER
