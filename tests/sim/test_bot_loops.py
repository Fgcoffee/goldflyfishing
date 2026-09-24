"""The things that kept a 50-game run at 49 of 50.

Each was one game grinding for minutes rather than a crash: an action offered
but unpayable and retried at every priority, a legal untap loop taken thousands
of times, a spell recast from the graveyard hundreds of times, an ability that
could not target a player. None tripped the action budget, because failed
attempts are not actions and the loops were not free.
"""

from __future__ import annotations

from types import SimpleNamespace

from mtgfish.ai.simple import SimpleAgent
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.ui.sandbox import Sandbox


def _game(turn=3, phase=2, step=5, objects=None):
    return SimpleNamespace(turn=turn, phase=phase, step=step, objects=objects or {})


def test_a_failed_action_is_not_retried_in_the_same_step():
    bot = SimpleAgent()
    game = _game()
    tried = Action(ActionKind.ACTIVATE_ABILITY, source=7, ability_index=0)
    bot.action_failed(game, 0, tried)
    bot._sync_window(game)
    assert bot._key(tried) in bot._failed


def test_the_bot_forgets_failures_when_the_step_moves_on():
    bot = SimpleAgent()
    tried = Action(ActionKind.CAST_SPELL, source=9)
    bot.action_failed(_game(step=5), 0, tried)
    bot._sync_window(_game(step=6))
    assert not bot._failed


def test_the_same_card_is_cast_at_most_so_many_times_a_turn():
    card = SimpleNamespace(card=SimpleNamespace(name="Strike It Rich"))
    game = _game(objects={4: card})
    bot = SimpleAgent()
    action = Action(ActionKind.CAST_SPELL, source=4)
    bot._casts[(game.turn, "Strike It Rich")] = SimpleAgent.MAX_CASTS_PER_TURN
    assert bot._cast_too_often(game, action)
    assert not bot._cast_too_often(_game(turn=4, objects={4: card}), action)


def test_an_unpayable_sacrifice_cost_is_not_offered(card_db):
    """Professional Face-Breaker: "Sacrifice a Treasure" with no Treasure."""
    box = Sandbox(db=card_db)
    box.put("Professional Face-Breaker", "battlefield", 0)
    box.give_mana(10, 0)
    offered = [a for a in box.legal(0) if a["name"] == "Professional Face-Breaker"]
    assert offered == []


def test_an_ability_can_target_a_player(card_db):
    """Walking Ballista, "any target", with the engine choosing the target."""
    box = Sandbox(db=card_db)
    box.put("Walking Ballista", "battlefield", 0)
    ballista = next(o for o in box.game.objects.values() if o.card and o.card.name == "Walking Ballista")
    ballista.add_counters("+1/+1", 2)
    box.game.invalidate_characteristics()

    pings = [
        a for a in box.legal(0)
        if a["name"] == "Walking Ballista" and a["kind"] == "ACTIVATE_ABILITY" and "damage" in a["description"].lower()
    ]
    assert pings, [a["description"] for a in box.legal(0)]
    before = ballista.counter_count("+1/+1")
    state = box.perform(pings[0]["index"], 0)
    assert not state["error"], state["error"]
    assert ballista.counter_count("+1/+1") == before - 1, "the counter is the cost"
    assert state["stack"], "the ability is on the stack with a target chosen"
