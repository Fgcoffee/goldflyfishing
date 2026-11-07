"""Toxic and poisonous from their real oracle text, on a three-player board.

Toxic was built as a trigger poisoning each opponent; CR 702.164c makes it a
static ability giving poison only to the player the creature dealt combat
damage to, as a result of that damage (CR 120.3g).
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.player import Player
from mtgfish.rules.cr500_turn_structure.cr506_combat import Combat, deal_combat_damage
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1, P2 = PlayerId(0), PlayerId(1), PlayerId(2)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    game = table.game
    game.players.append(Player(id=P2, name="Third", life=40))
    game.turn_order = [P0, P1, P2]
    for seat in (P1, P2):
        game.agents[seat] = PassiveOpponent()
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def attacker(box, name):
    box.put(name, "battlefield", 0)
    (obj,) = [o for o in box.game.objects.values()
              if o.is_live and o.card is not None and o.card.name == name]
    return obj


def hit(box, *attackers, defender=P1):
    game = box.game
    game.active_player = P0
    game.combat = Combat(attacking={a.id: defender for a in attackers})
    deal_combat_damage(game)


@pytest.mark.parametrize("name", ["Crawling Chorus", "Duelist of Deep Faith"])
def test_toxic_poisons_only_the_player_it_damaged(box, name):
    need(box, name)
    hit(box, attacker(box, name))
    game = box.game
    # CR 120.3g: part of the damage's result - nothing on the stack to wait for.
    assert [p.poison for p in game.players] == [0, 1, 0]
    assert game.player(P1).life < 40
    assert game.player(P2).life == 40
    # And nothing waiting to poison anyone else.
    box.settle()
    while game.stack:
        box.resolve_top()
    assert [p.poison for p in game.players] == [0, 1, 0]


def test_toxic_follows_the_attack(box):
    need(box, "Crawling Chorus")
    hit(box, attacker(box, "Crawling Chorus"), defender=P2)
    assert [p.poison for p in box.game.players] == [0, 0, 1]


def test_toxic_without_combat_damage_gives_none(box):
    """Control: only combat damage to a player carries toxic's poison."""
    need(box, "Crawling Chorus")
    from mtgfish.rules.cr100_game_concepts.actions import deal_damage

    chorus = attacker(box, "Crawling Chorus")
    deal_damage(box.game, P1, 1, source=chorus.id, source_controller=P0)
    assert [p.poison for p in box.game.players] == [0, 0, 0]
