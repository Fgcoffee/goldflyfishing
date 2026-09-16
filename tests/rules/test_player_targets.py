"""A targeted player is one player.

"Target opponent" and "target player" name a player chosen when the spell or
ability goes on the stack. Three pieces of the engine disagreed about that:

* triggered abilities never offered players as candidates, so Sanguine Bond
  went on the stack with no target at all;
* the resolver ignored whatever was chosen and resolved the scope instead -
  "target opponent" as every opponent, "target player" as nobody;
* and the bot could not tell its own player target from anyone else's, so
  "target player loses 1 life" was aimed at itself.

In a two-player game the first two are invisible: the only opponent is every
opponent. Found by the loop tests, where one Sanguine Bond drain took all three
opponents in a four-player pod from 40 to 39.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules import actions
from mtgfish.rules.enums import Zone
from mtgfish.rules.ids import player_target
from mtgfish.rules.player import Player
from mtgfish.rules.priority import run_priority, settle
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _pod(box):
    """Seats two more opponents: a Commander table."""
    game = box.game
    for index in (2, 3):
        game.players.append(Player(id=index, name=f"Opponent {index}"))
        game.players[index].life = 40
        game.turn_order.append(index)
        game.agents[index] = PassiveOpponent()
    return game


def test_target_opponent_is_one_opponent(box):
    _need(box, "Sanguine Bond")
    game = _pod(box)
    box.put("Sanguine Bond", "battlefield", 0)

    actions.gain_life(game, 0, 1)
    settle(game)
    trigger = game.objects[game.stack[-1]]
    assert trigger.targets and trigger.targets[0], "the trigger chose no target"

    box.resolve_top()
    assert [p.life for p in game.players] == [41, 39, 40, 40]


def test_target_player_is_somebody(box):
    """Sign in Blood: "target player draws two cards". It drew none."""
    _need(box, "Sign in Blood", "Forest")
    from mtgfish.rules.legality import legal_actions
    from mtgfish.rules.priority import _perform

    game = box.game
    for _ in range(5):
        box.put("Forest", "library", 0)
    box.put("Sign in Blood", "hand", 0)
    box.give_mana(5, 0)

    cast = next(
        action
        for action in legal_actions(game, 0)
        if action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == "Sign in Blood"
    )
    assert _perform(game, 0, cast)
    box.resolve_top()

    assert len(game.player(0).hand) == 2
    assert len(game.player(0).library) == 3


def test_an_aristocrat_drains_an_opponent_not_its_controller(box):
    """Blood Artist says "target player", and the bot is the one choosing."""
    _need(box, "Blood Artist", "Grizzly Bears")
    from mtgfish.ai.simple import SimpleAgent

    game = box.game
    game.agents[0] = SimpleAgent()
    box.put("Blood Artist", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    bear = next(
        o
        for o in game.objects.values()
        if o.card.name == "Grizzly Bears" and o.zone is Zone.BATTLEFIELD
    )

    actions.destroy(game, bear)
    run_priority(game)

    assert [p.life for p in game.players] == [41, 39]


def test_the_bot_aims_harm_at_the_weakest_opponent_and_help_at_itself(box):
    from mtgfish.ai.simple import SimpleAgent

    game = _pod(box)
    game.player(2).life = 7
    agent = SimpleAgent()
    everyone = [player_target(index) for index in range(4)]

    assert agent._best(game, 0, everyone, harmful=True) == player_target(2)
    assert agent._best(game, 0, everyone, harmful=False) == player_target(0)


def test_a_player_who_has_left_is_not_hit(box):
    """CR 608.2b: the chosen opponent lost in response; the drain does nothing."""
    _need(box, "Sanguine Bond")
    game = _pod(box)
    box.put("Sanguine Bond", "battlefield", 0)

    actions.gain_life(game, 0, 1)
    settle(game)
    trigger = game.objects[game.stack[-1]]
    (chosen,) = trigger.targets[0]
    from mtgfish.rules.enums import LossReason
    from mtgfish.rules.ids import target_player

    game.player_loses(target_player(chosen), LossReason.LIFE)
    box.resolve_top()

    assert [p.life for p in game.players if not p.has_lost] == [41, 40, 40]
